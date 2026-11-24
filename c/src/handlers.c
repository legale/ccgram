#include "ccgram.h"

#include <ctype.h>
#include <errno.h>
#include <libgen.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

/* ── Cleanup ───────────────────────────────────────────────────────── */

void clear_topic_state(int64_t user_id, int64_t thread_id, const char *window_id)
{
	int64_t chat_id = router_resolve_chat_id(user_id, thread_id);
	char key[128];

	live_clear_view(user_id, thread_id);
	diff_clear_topic(thread_id);
	topic_tail_clear(chat_id, thread_id);
	if (window_id && *window_id) {
		tmux_forget_sidecar_pane(window_id);
		snprintf(key, sizeof(key), "topic-probe:%s", window_id);
		log_throttle_reset(key);
	}
	snprintf(key, sizeof(key), "status-update:%lld:%lld", (long long)user_id, (long long)thread_id);
	log_throttle_reset(key);
}

/* ── Helpers ───────────────────────────────────────────────────────── */

static bool valid_name(const char *name)
{
	size_t len;

	if (!name || !*name)
		return false;
	len = strlen(name);
	if (len > 50)
		return false;
	return strchr(name, '\n') == NULL;
}

static void extract_session_name(const char *window_id, char *out, size_t cap)
{
	const char *colon;

	if (window_id[0] != '@') {
		colon = strrchr(window_id, ':');
		if (colon) {
			size_t n = (size_t)(colon - window_id);
			if (n >= cap)
				n = cap - 1;
			memcpy(out, window_id, n);
			out[n] = '\0';
			return;
		}
	}
	snprintf(out, cap, "%s", g_cfg.tmux_session_name);
}

static bool find_topic_session(int64_t chat_id, int64_t thread_id, struct tmux_window *out)
{
	struct tmux_window sessions[128];
	size_t n = tmux_list_sessions(sessions, 128);
	int match_cnt = 0;
	size_t match_idx = 0;

	for (size_t i = 0; i < n; i++) {
		if (sessions[i].has_topic_ref &&
		    sessions[i].topic_chat_id == chat_id &&
		    sessions[i].topic_thread_id == thread_id) {
			match_idx = i;
			match_cnt++;
		}
	}
	if (match_cnt == 1) {
		*out = sessions[match_idx];
		return true;
	}
	match_cnt = 0;
	for (size_t i = 0; i < n; i++) {
		if (sessions[i].has_topic_ref && sessions[i].topic_thread_id == thread_id) {
			match_idx = i;
			match_cnt++;
		}
	}
	if (match_cnt == 1) {
		*out = sessions[match_idx];
		return true;
	}
	return false;
}

static void bind_runtime(int64_t user_id, int64_t chat_id, int64_t thread_id, const struct tmux_window *s)
{
	char tname[128];

	tmux_topic_name_from_session(s->window_name, tname, sizeof(tname));
	router_bind_thread(user_id, thread_id, s->window_id, tname);
	router_set_group_chat_id(user_id, thread_id, chat_id);
}

static bool resolve_topic_window(int64_t user_id, int64_t chat_id, int64_t thread_id, char *out, size_t cap)
{
	struct tmux_window s;

	if (router_get_window_for_thread(user_id, thread_id, out, cap))
		return true;
	if (!find_topic_session(chat_id, thread_id, &s))
		return false;
	bind_runtime(user_id, chat_id, thread_id, &s);
	snprintf(out, cap, "%s", s.window_id);
	return true;
}

static void drop_runtime_ref(int64_t chat_id, int64_t thread_id)
{
	struct thread_binding bindings[256];
	size_t n = router_iter_bindings(bindings, 256);

	for (size_t i = 0; i < n; i++) {
		if (bindings[i].thread_id != thread_id)
			continue;
		if (router_resolve_chat_id(bindings[i].user_id, thread_id) == chat_id)
			router_unbind_thread(bindings[i].user_id, thread_id, NULL, 0);
	}
}

static bool ensure_topic_session(int64_t user_id, int64_t chat_id, int64_t thread_id,
				 const char *raw_topic_name, bool rebind,
				 char *out_wid, size_t wid_cap, char *err_buf, size_t err_cap)
{
	struct tmux_window sessions[128];
	size_t n = tmux_list_sessions(sessions, 128);
	int linked_idx = -1, linked_cnt = 0;
	char topic_name[128];

	for (size_t i = 0; i < n; i++) {
		if (sessions[i].has_topic_ref &&
		    sessions[i].topic_chat_id == chat_id &&
		    sessions[i].topic_thread_id == thread_id) {
			linked_idx = (int)i;
			linked_cnt++;
		}
	}
	if (linked_cnt > 1) {
		snprintf(err_buf, err_cap, "Multiple tmux sessions are bound to Telegram topic %lld.", (long long)thread_id);
		return false;
	}

	snprintf(topic_name, sizeof(topic_name), "%s", raw_topic_name ? raw_topic_name : "");
	char *ts = topic_name;
	while (*ts && isspace((unsigned char)*ts)) ts++;
	if (ts != topic_name) memmove(topic_name, ts, strlen(ts) + 1);
	size_t tlen = strlen(topic_name);
	while (tlen > 0 && isspace((unsigned char)topic_name[tlen - 1]))
		topic_name[--tlen] = '\0';

	if (!*topic_name) {
		if (linked_cnt == 1) {
			bind_runtime(user_id, chat_id, thread_id, &sessions[linked_idx]);
			snprintf(out_wid, wid_cap, "%s", sessions[linked_idx].window_id);
			return true;
		}
		snprintf(err_buf, err_cap, "Topic is not bound. Use `//bind <key>`.");
		return false;
	}
	if (!valid_name(topic_name)) {
		snprintf(err_buf, err_cap, "Invalid session name.");
		return false;
	}

	char target[128];
	tmux_topic_session_name(topic_name, target, sizeof(target));

	int target_idx = -1;
	for (size_t i = 0; i < n; i++) {
		if (strcmp(sessions[i].window_name, target) == 0) {
			target_idx = (int)i;
			break;
		}
	}

	if (linked_cnt == 1 && strcmp(sessions[linked_idx].window_name, target) == 0) {
		bind_runtime(user_id, chat_id, thread_id, &sessions[linked_idx]);
		snprintf(out_wid, wid_cap, "%s", sessions[linked_idx].window_id);
		return true;
	}

	if (!rebind) {
		if (linked_cnt == 1) {
			bind_runtime(user_id, chat_id, thread_id, &sessions[linked_idx]);
			snprintf(out_wid, wid_cap, "%s", sessions[linked_idx].window_id);
			return true;
		}
		if (target_idx >= 0 && sessions[target_idx].has_topic_ref) {
			snprintf(err_buf, err_cap, "Session %s is already bound to another Telegram topic.", target);
			return false;
		}
	}

	bool created = false;
	struct tmux_window target_win;
	if (target_idx < 0) {
		char cerr[256] = {0};
		memset(&target_win, 0, sizeof(target_win));
		if (!tmux_create_session(target, g_cfg.session_working_directory, topic_name,
					 target_win.window_id, sizeof(target_win.window_id), cerr, sizeof(cerr))) {
			snprintf(err_buf, err_cap, "Failed to create tmux session %s: %s", target, cerr);
			return false;
		}
		snprintf(target_win.window_name, sizeof(target_win.window_name), "%s", target);
		created = true;
	} else {
		target_win = sessions[target_idx];
	}

	if (linked_cnt == 1 && strcmp(sessions[linked_idx].window_name, target) != 0) {
		if (!tmux_clear_session_topic(sessions[linked_idx].window_name)) {
			if (created)
				tmux_kill_session(target);
			snprintf(err_buf, err_cap, "Failed to unbind tmux session %s.", sessions[linked_idx].window_name);
			return false;
		}
	}

	if (!tmux_set_session_topic(target, chat_id, thread_id)) {
		if (linked_cnt == 1 && strcmp(sessions[linked_idx].window_name, target) != 0)
			tmux_set_session_topic(sessions[linked_idx].window_name, chat_id, thread_id);
		if (created)
			tmux_kill_session(target);
		snprintf(err_buf, err_cap, "Failed to bind tmux session %s.", target);
		return false;
	}

	if (linked_cnt == 1 && strcmp(sessions[linked_idx].window_name, target) != 0)
		drop_runtime_ref(chat_id, thread_id);
	if (target_win.has_topic_ref &&
	    (target_win.topic_chat_id != chat_id || target_win.topic_thread_id != thread_id)) {
		drop_runtime_ref(target_win.topic_chat_id, target_win.topic_thread_id);
	}

	bind_runtime(user_id, chat_id, thread_id, &target_win);
	snprintf(out_wid, wid_cap, "%s", target_win.window_id);
	return true;
}

static bool kill_session_and_unbind(const char *window_id, char *out_msg, size_t msg_cap)
{
	char sname[128];
	struct tmux_window sessions[128];
	size_t n;

	extract_session_name(window_id, sname, sizeof(sname));
	n = tmux_list_sessions(sessions, 128);
	for (size_t i = 0; i < n; i++) {
		if (strcmp(sessions[i].window_name, sname) == 0 && sessions[i].has_topic_ref) {
			struct tg_error err;
			if (!tg_delete_forum_topic(sessions[i].topic_chat_id, sessions[i].topic_thread_id, &err)) {
				if (!tg_is_thread_gone(&err)) {
					snprintf(out_msg, msg_cap, "Telegram topic was not deleted: %.180s", err.description);
					return false;
				}
			}
			break;
		}
	}

	if (!tmux_kill_session(sname)) {
		snprintf(out_msg, msg_cap, "Session `%s` was not killed.", sname);
		return false;
	}

	struct thread_binding bindings[256];
	size_t nb = router_iter_bindings(bindings, 256);
	for (size_t i = 0; i < nb; i++) {
		if (strcmp(bindings[i].window_id, window_id) == 0) {
			clear_topic_state(bindings[i].user_id, bindings[i].thread_id, window_id);
			router_unbind_thread(bindings[i].user_id, bindings[i].thread_id, NULL, 0);
		}
	}

	snprintf(out_msg, msg_cap, "%s", sname);
	return true;
}

/* ── Sessions Dashboard ────────────────────────────────────────────── */

static void build_dashboard(int64_t user_id, char **out_text, struct tg_keyboard *kb)
{
	int64_t bound_tids[256];
	char bound_wids[256][192];
	size_t nb = router_get_all_thread_windows(user_id, bound_tids, bound_wids, 256);
	struct tmux_window sessions[128];
	size_t ns = tmux_list_sessions(sessions, 128);

	memset(kb, 0, sizeof(*kb));
	if (ns == 0) {
		*out_text = xstrdup("No active sessions.\n\nCreate a new topic to start a session.");
		return;
	}

	struct strbuf lines;
	sb_init(&lines);

	for (size_t i = 0; i < ns && kb->num_rows < 15; i++) {
		const char *wid = sessions[i].window_id;
		bool is_bound = sessions[i].has_topic_ref;
		char prefix_colon[140];

		snprintf(prefix_colon, sizeof(prefix_colon), "%s:", sessions[i].window_name);
		for (size_t j = 0; j < nb; j++) {
			if (strcmp(bound_wids[j], sessions[i].window_id) == 0 ||
			    strncmp(bound_wids[j], prefix_colon, strlen(prefix_colon)) == 0) {
				wid = bound_wids[j];
				is_bound = true;
				break;
			}
		}

		const char *st = is_bound ? "+" : "o";
		const char *sfx = is_bound ? "" : " (unbound)";
		if (sessions[i].cwd[0])
			sb_appendf(&lines, "%s %s \xe2\x80\x94 %s%s\n", st, sessions[i].window_name, sessions[i].cwd, sfx);
		else
			sb_appendf(&lines, "%s %s%s\n", st, sessions[i].window_name, sfx);

		size_t r = kb->num_rows++;
		kb->row_lens[r] = 3;
		snprintf(kb->rows[r][0].text, 96, "\xe2\x9c\x8f\xef\xb8\x8f %.80s", sessions[i].window_name);
		snprintf(kb->rows[r][0].callback_data, CCGRAM_MAX_CB_DATA + 1, "%s%.54s", CB_SESSIONS_RENAME, wid);
		snprintf(kb->rows[r][1].text, 96, "\xf0\x9f\x93\xb8");
		snprintf(kb->rows[r][1].callback_data, CCGRAM_MAX_CB_DATA + 1, "%s%.54s", CB_SESSIONS_SCREENSHOT, wid);
		snprintf(kb->rows[r][2].text, 96, "\xe2\x9c\x95");
		snprintf(kb->rows[r][2].callback_data, CCGRAM_MAX_CB_DATA + 1, "%s%.53s", CB_SESSIONS_KILL, wid);
	}

	size_t rr = kb->num_rows++;
	kb->row_lens[rr] = 1;
	snprintf(kb->rows[rr][0].text, 96, "Refresh");
	snprintf(kb->rows[rr][0].callback_data, CCGRAM_MAX_CB_DATA + 1, "%s", CB_SESSIONS_REFRESH);

	if (lines.len > 0 && lines.data[lines.len - 1] == '\n')
		lines.data[--lines.len] = '\0';

	struct strbuf msg;
	sb_init(&msg);
	sb_appendf(&msg,
		   "Sessions\n\n```\n%s\n```\n"
		   "+ \xd0\xbf\xd1\x80\xd0\xb8\xd0\xb2\xd1\x8f\xd0\xb7\xd0\xb0\xd0\xbd\xd0\xb0 \xd0\xba \xd1\x82\xd0\xbe\xd0\xbf\xd0\xb8\xd0\xba\xd1\x83 | o \xd1\x81\xd0\xb2\xd0\xbe\xd0\xb1\xd0\xbe\xd0\xb4\xd0\xbd\xd0\xb0\n"
		   "\xe2\x9c\x8f\xef\xb8\x8f \xd0\xbf\xd0\xb5\xd1\x80\xd0\xb5\xd0\xb8\xd0\xbc\xd0\xb5\xd0\xbd\xd0\xbe\xd0\xb2\xd0\xb0\xd1\x82\xd1\x8c | \xf0\x9f\x93\xb8 \xd1\x81\xd0\xba\xd1\x80\xd0\xb8\xd0\xbd\xd1\x88\xd0\xbe\xd1\x82 | \xe2\x9c\x95 \xd0\xb7\xd0\xb0\xd0\xba\xd1\x80\xd1\x8b\xd1\x82\xd1\x8c",
		   lines.data ? lines.data : "");
	sb_free(&lines);
	*out_text = sb_detach(&msg);
}

static void handle_sessions_callback(const struct tg_update *u)
{
	const char *data = u->callback_data;

	if (strcmp(data, CB_SESSIONS_REFRESH) == 0) {
		char *text = NULL;
		struct tg_keyboard kb;
		build_dashboard(u->user_id, &text, &kb);
		tg_safe_edit(u->chat_id, u->message_id, text, &kb);
		free(text);
		tg_answer_callback(u->callback_id, "Refreshed", false);
		return;
	}
	if (strncmp(data, CB_SESSIONS_SCREENSHOT, strlen(CB_SESSIONS_SCREENSHOT)) == 0) {
		struct tg_update mod = *u;
		snprintf(mod.callback_data, sizeof(mod.callback_data), "%s%s",
			 CB_STATUS_SCREENSHOT, data + strlen(CB_SESSIONS_SCREENSHOT));
		live_handle_callback(&mod, true);
		return;
	}
	if (strncmp(data, CB_SESSIONS_KILL_CONFIRM, strlen(CB_SESSIONS_KILL_CONFIRM)) == 0) {
		const char *wid = data + strlen(CB_SESSIONS_KILL_CONFIRM);
		char display[128], msg[256];
		router_get_display_name(wid, display, sizeof(display));
		if (!kill_session_and_unbind(wid, msg, sizeof(msg))) {
			tg_safe_edit(u->chat_id, u->message_id, msg, NULL);
			tg_answer_callback(u->callback_id, "Session not found", false);
			return;
		}
		char *dtext = NULL;
		struct tg_keyboard kb;
		build_dashboard(u->user_id, &dtext, &kb);
		struct strbuf sb;
		sb_init(&sb);
		sb_appendf(&sb, "Killed '%s'\n\n%s", display, dtext);
		tg_safe_edit(u->chat_id, u->message_id, sb.data, &kb);
		sb_free(&sb);
		free(dtext);
		tg_answer_callback(u->callback_id, "Killed", false);
		return;
	}
	if (strncmp(data, CB_SESSIONS_KILL, strlen(CB_SESSIONS_KILL)) == 0) {
		const char *wid = data + strlen(CB_SESSIONS_KILL);
		char display[128], prompt[256];
		struct tg_keyboard kb;
		router_get_display_name(wid, display, sizeof(display));
		memset(&kb, 0, sizeof(kb));
		kb.num_rows = 2;
		kb.row_lens[0] = 1;
		snprintf(kb.rows[0][0].text, 96, "Confirm kill %.70s", display);
		snprintf(kb.rows[0][0].callback_data, CCGRAM_MAX_CB_DATA + 1, "%s%.51s", CB_SESSIONS_KILL_CONFIRM, wid);
		kb.row_lens[1] = 1;
		snprintf(kb.rows[1][0].text, 96, "Refresh");
		snprintf(kb.rows[1][0].callback_data, CCGRAM_MAX_CB_DATA + 1, "%s", CB_SESSIONS_REFRESH);
		snprintf(prompt, sizeof(prompt), "Kill session '%s'?", display);
		tg_safe_edit(u->chat_id, u->message_id, prompt, &kb);
		tg_answer_callback(u->callback_id, NULL, false);
		return;
	}
	if (strncmp(data, CB_SESSIONS_RENAME, strlen(CB_SESSIONS_RENAME)) == 0) {
		const char *wid = data + strlen(CB_SESSIONS_RENAME);
		char sname[128], display[128], prompt[256];
		extract_session_name(wid, sname, sizeof(sname));
		tmux_topic_name_from_session(sname, display, sizeof(display));

		int64_t tids[256];
		char wids[256][192];
		size_t nb = router_get_all_thread_windows(u->user_id, tids, wids, 256);
		int64_t tid = 0;
		for (size_t i = 0; i < nb; i++) {
			if (strcmp(wids[i], wid) == 0) {
				tid = tids[i];
				break;
			}
		}
		user_state_set_rename(u->user_id, tid, wid);
		snprintf(prompt, sizeof(prompt), "Enter new name for session `%.64s`:", display);
		if (tid > 0 && u->chat_id != 0) {
			struct tg_error err;
			tg_send_message_plain(u->chat_id, tid, 0, prompt, NULL, 0, NULL, true, NULL, &err);
		} else {
			tg_safe_edit(u->chat_id, u->message_id, prompt, NULL);
		}
		tg_answer_callback(u->callback_id, "Rename session", false);
	}
}

/* ── // Commands ───────────────────────────────────────────────────── */

static void cmd_commands(const struct tg_update *u, const char *args);

static void cmd_sessions(const struct tg_update *u, const char *args)
{
	(void)args;
	char *text = NULL;
	struct tg_keyboard kb;

	build_dashboard(u->user_id, &text, &kb);
	tg_safe_reply(u->chat_id, u->has_thread_id ? u->thread_id : 0, u->message_id, text, &kb);
	free(text);
}

static bool prepare_canonical_session(const char *raw_name, char *topic_name, size_t tcap,
				      char *canon_sess, size_t ccap, char *err_buf, size_t ecap)
{
	char has_err[256] = {0};
	size_t plen = strlen(g_cfg.tmux_session_prefix);

	if (!tmux_session_exists(raw_name, has_err, sizeof(has_err))) {
		snprintf(err_buf, ecap, "%s", has_err[0] ? has_err : "can't find session");
		return false;
	}
	if (strncmp(raw_name, g_cfg.tmux_session_prefix, plen) == 0) {
		snprintf(topic_name, tcap, "%s", raw_name + plen);
		snprintf(canon_sess, ccap, "%s", raw_name);
	} else {
		snprintf(topic_name, tcap, "%s", raw_name);
		tmux_topic_session_name(topic_name, canon_sess, ccap);
	}
	if (!valid_name(topic_name)) {
		snprintf(err_buf, ecap, "Invalid session name: %s", raw_name);
		return false;
	}
	if (strcmp(raw_name, canon_sess) != 0) {
		if (tmux_session_exists(canon_sess, NULL, 0)) {
			snprintf(err_buf, ecap, "Error: target session '%s' already exists", canon_sess);
			return false;
		}
		if (!tmux_rename_session(raw_name, canon_sess)) {
			snprintf(err_buf, ecap, "Failed to rename tmux session '%s' to '%s'", raw_name, canon_sess);
			return false;
		}
	}
	return true;
}

static void do_bind_raw(const struct tg_update *u, const char *raw_name)
{
	if (!u->has_thread_id) {
		tg_safe_reply(u->chat_id, 0, u->message_id, "Error: not inside a topic", NULL);
		return;
	}
	char topic_name[128], canon[128], err[256] = {0}, wid[192];
	if (!prepare_canonical_session(raw_name, topic_name, sizeof(topic_name),
				       canon, sizeof(canon), err, sizeof(err))) {
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, err, NULL);
		return;
	}
	tmux_switch_client(canon);
	if (!ensure_topic_session(u->user_id, u->chat_id, u->thread_id, topic_name, true,
				  wid, sizeof(wid), err, sizeof(err))) {
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, err, NULL);
		return;
	}
	struct tg_error terr;
	tg_edit_forum_topic(u->chat_id, u->thread_id, topic_name, &terr);
	tg_safe_reply(u->chat_id, u->thread_id, u->message_id, canon, NULL);
}

static void cmd_bindraw(const struct tg_update *u, const char *args)
{
	if (!args || !*args || strchr(args, ' ') != NULL) {
		tg_safe_reply(u->chat_id, u->has_thread_id ? u->thread_id : 0, u->message_id,
			      "Usage: //bindraw <session>", NULL);
		return;
	}
	do_bind_raw(u, args);
}

static void cmd_bind(const struct tg_update *u, const char *args)
{
	if (!args || !*args || strchr(args, ' ') != NULL || !valid_name(args)) {
		tg_safe_reply(u->chat_id, u->has_thread_id ? u->thread_id : 0, u->message_id,
			      "Usage: //bind <key>", NULL);
		return;
	}
	char target[128];
	tmux_topic_session_name(args, target, sizeof(target));
	if (!tmux_session_exists(target, NULL, 0) && tmux_session_exists(args, NULL, 0))
		snprintf(target, sizeof(target), "%s", args);
	do_bind_raw(u, target);
}

static void cmd_new(const struct tg_update *u, const char *args)
{
	if (!u->has_thread_id) {
		tg_safe_reply(u->chat_id, 0, u->message_id, "Error: not inside a topic", NULL);
		return;
	}
	if (!args || !*args || strchr(args, ' ') != NULL || !valid_name(args)) {
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, "Usage: //new <key>", NULL);
		return;
	}
	char target[128], wid[192], err[256] = {0};
	tmux_topic_session_name(args, target, sizeof(target));
	if (!tmux_create_session(target, g_cfg.session_working_directory, args,
				 wid, sizeof(wid), err, sizeof(err))) {
		char msg[512];
		snprintf(msg, sizeof(msg), "Failed to create tmux session %s: %s", target, err);
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, msg, NULL);
		return;
	}
	tg_safe_reply(u->chat_id, u->thread_id, u->message_id, target, NULL);
}

static void cmd_rename(const struct tg_update *u, const char *args)
{
	if (!u->has_thread_id) {
		tg_safe_reply(u->chat_id, 0, u->message_id, "Error: not inside a topic", NULL);
		return;
	}
	if (!args || !*args) {
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, "Usage: //rename <new_name>", NULL);
		return;
	}
	char name[128];
	tmux_topic_name_from_session(args, name, sizeof(name));
	if (!valid_name(name)) {
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, "Invalid topic name.", NULL);
		return;
	}

	struct tmux_window s;
	if (!find_topic_session(u->chat_id, u->thread_id, &s)) {
		char wid[192];
		if (!router_get_window_for_thread(u->user_id, u->thread_id, wid, sizeof(wid)) &&
		    !router_get_window_for_chat_thread(u->chat_id, u->thread_id, wid, sizeof(wid))) {
			tg_safe_reply(u->chat_id, u->thread_id, u->message_id,
				      "Topic is not bound to any session. Use //bind <key>.", NULL);
			return;
		}
		if (!tmux_find_window_by_id(wid, &s)) {
			tg_safe_reply(u->chat_id, u->thread_id, u->message_id,
				      "Topic is not bound to any session. Use //bind <key>.", NULL);
			return;
		}
		extract_session_name(wid, s.window_name, sizeof(s.window_name));
	}

	char target[128], old_name[128];
	snprintf(old_name, sizeof(old_name), "%s", s.window_name);
	tmux_topic_session_name(name, target, sizeof(target));
	if (strcmp(old_name, target) != 0) {
		if (!tmux_rename_session(old_name, target)) {
			char msg[300];
			snprintf(msg, sizeof(msg), "Failed to rename tmux session %s -> %s", old_name, target);
			tg_safe_reply(u->chat_id, u->thread_id, u->message_id, msg, NULL);
			return;
		}
		const char *colon = strrchr(s.window_id, ':');
		char bare[64] = {0};
		if (colon)
			snprintf(bare, sizeof(bare), "%s", colon + 1);
		snprintf(s.window_name, sizeof(s.window_name), "%s", target);
		if (bare[0])
			snprintf(s.window_id, sizeof(s.window_id), "%s:%s", target, bare);
		tmux_set_session_topic(target, u->chat_id, u->thread_id);
	}
	bind_runtime(u->user_id, u->chat_id, u->thread_id, &s);
	tg_edit_forum_topic(u->chat_id, u->thread_id, name, NULL);

	char reply[300];
	snprintf(reply, sizeof(reply), "%s (\xd1\x82\xd0\xbe\xd0\xbf\xd0\xb8\xd0\xba: %s)", target, name);
	tg_safe_reply(u->chat_id, u->thread_id, u->message_id, reply, NULL);
}

static void cmd_unbind(const struct tg_update *u, const char *args)
{
	(void)args;
	if (!u->has_thread_id) {
		tg_safe_reply(u->chat_id, 0, u->message_id, "Use this command inside a topic.", NULL);
		return;
	}
	int64_t chat_id = router_resolve_chat_id(u->user_id, u->thread_id);
	if (chat_id == u->user_id && u->chat_id != 0)
		chat_id = u->chat_id;

	struct tmux_window s;
	if (!find_topic_session(chat_id, u->thread_id, &s)) {
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id,
			      "This topic is not bound to a managed session.", NULL);
		return;
	}
	if (!tmux_clear_session_topic(s.window_name)) {
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, "Cannot clear tmux topic metadata.", NULL);
		return;
	}
	clear_topic_state(u->user_id, u->thread_id, s.window_id);
	router_unbind_thread(u->user_id, u->thread_id, NULL, 0);

	struct tg_error err;
	if (!tg_close_forum_topic(chat_id, u->thread_id, &err)) {
		tmux_set_session_topic(s.window_name, chat_id, u->thread_id);
		bind_runtime(u->user_id, chat_id, u->thread_id, &s);
		char msg[320];
		snprintf(msg, sizeof(msg), "Cannot close topic: %s", err.description);
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, msg, NULL);
		return;
	}
	char msg[256];
	snprintf(msg, sizeof(msg), "Unbound topic. Session `%s` is still running.", s.window_name);
	tg_safe_reply(u->chat_id, u->thread_id, u->message_id, msg, NULL);
}

static void cmd_killme(const struct tg_update *u, const char *args)
{
	(void)args;
	if (!u->has_thread_id) {
		tg_safe_reply(u->chat_id, 0, u->message_id, "Use this command inside a topic.", NULL);
		return;
	}
	char wid[192] = {0};
	if (!resolve_topic_window(u->user_id, u->chat_id, u->thread_id, wid, sizeof(wid))) {
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id,
			      "This topic is not bound to a managed session.", NULL);
		return;
	}
	char msg[256];
	if (!kill_session_and_unbind(wid, msg, sizeof(msg)))
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, msg, NULL);
}

static void cmd_screenshot_wrap(const struct tg_update *u, const char *args)
{
	(void)args;
	cmd_screenshot(u);
}

static void cmd_live_wrap(const struct tg_update *u, const char *args)
{
	(void)args;
	cmd_live(u);
}

static void cmd_arrows(const struct tg_update *u, const char *args)
{
	(void)args;
	if (!u->has_thread_id) {
		tg_safe_reply(u->chat_id, 0, u->message_id, "Use this command inside a topic.", NULL);
		return;
	}
	int64_t chat_id = router_resolve_chat_id(u->user_id, u->thread_id);
	if (chat_id == u->user_id && u->chat_id != 0)
		chat_id = u->chat_id;

	int res = diff_toggle_arrows(chat_id, u->thread_id);
	if (res == -1)
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, "No screen delta message in this topic.", NULL);
	else if (res == -2)
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, "Failed to update screen delta buttons.", NULL);
	else
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, res ? "arrows on" : "arrows off", NULL);
}

static void send_special_key(const struct tg_update *u, const char *key, const char *key_name, int repeat)
{
	if (!u->has_thread_id) {
		tg_safe_reply(u->chat_id, 0, u->message_id, "Use this command inside a topic.", NULL);
		return;
	}
	char wid[192];
	if (!resolve_topic_window(u->user_id, u->chat_id, u->thread_id, wid, sizeof(wid))) {
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id,
			      "This topic is not bound to a managed session.", NULL);
		return;
	}
	char spane[32];
	if (tmux_get_sidecar_pane_id(wid, spane, sizeof(spane))) {
		for (int i = 0; i < repeat; i++)
			tmux_send_keys_to_pane(spane, key, false, false, wid);
	}
	bool sent = true;
	for (int i = 0; i < repeat; i++) {
		sent = tmux_send_keys(wid, key, false, false);
		if (!sent)
			break;
	}
	char msg[128];
	if (!sent)
		snprintf(msg, sizeof(msg), "Failed to send `%s` to tmux.", key_name);
	else
		snprintf(msg, sizeof(msg), "Sent `%s`", key_name);
	tg_safe_reply(u->chat_id, u->thread_id, u->message_id, msg, NULL);
}

static void cmd_ctrl_c(const struct tg_update *u, const char *args) { (void)args; send_special_key(u, "C-c", "Ctrl+C", 1); }
static void cmd_ctrl_cc(const struct tg_update *u, const char *args) { (void)args; send_special_key(u, "C-c", "Ctrl+C x2", 2); }
static void cmd_enter(const struct tg_update *u, const char *args) { (void)args; send_special_key(u, "Enter", "Enter", 1); }
static void cmd_esc(const struct tg_update *u, const char *args) { (void)args; send_special_key(u, "Escape", "Escape", 1); }
static void cmd_tab(const struct tg_update *u, const char *args) { (void)args; send_special_key(u, "Tab", "Tab", 1); }

static bool is_image_ext(const char *path)
{
	const char *dot = strrchr(path, '.');
	if (!dot)
		return false;
	return strcasecmp(dot, ".png") == 0 || strcasecmp(dot, ".jpg") == 0 ||
	       strcasecmp(dot, ".jpeg") == 0 || strcasecmp(dot, ".gif") == 0 ||
	       strcasecmp(dot, ".webp") == 0;
}

static void cmd_send(const struct tg_update *u, const char *args)
{
	if (!u->has_thread_id) {
		tg_safe_reply(u->chat_id, 0, u->message_id, "Use this command inside a topic.", NULL);
		return;
	}
	if (!args || !*args) {
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, "Usage: //send <path_to_file>", NULL);
		return;
	}
	char wid[192];
	if (!resolve_topic_window(u->user_id, u->chat_id, u->thread_id, wid, sizeof(wid))) {
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, "No session bound to this topic.", NULL);
		return;
	}
	struct tmux_window w;
	if (!tmux_find_window_by_id(wid, &w) || !w.cwd[0] || !path_is_dir(w.cwd)) {
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, "Session working directory not available.", NULL);
		return;
	}

	char raw_target[1024], resolved[PATH_MAX], cwd_real[PATH_MAX], tmp_real[PATH_MAX];
	if (args[0] == '/')
		snprintf(raw_target, sizeof(raw_target), "%s", args);
	else
		snprintf(raw_target, sizeof(raw_target), "%s/%s", w.cwd, args);

	if (!realpath(raw_target, resolved) || !path_is_file(resolved)) {
		char msg[512];
		snprintf(msg, sizeof(msg), "File not found: %s", args);
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, msg, NULL);
		return;
	}
	if (!realpath(w.cwd, cwd_real))
		snprintf(cwd_real, sizeof(cwd_real), "%s", w.cwd);
	if (!realpath("/tmp", tmp_real))
		snprintf(tmp_real, sizeof(tmp_real), "/tmp");

	size_t clen = strlen(cwd_real), tlen = strlen(tmp_real);
	bool in_cwd = strncmp(resolved, cwd_real, clen) == 0 && (resolved[clen] == '/' || resolved[clen] == '\0');
	bool in_tmp = strncmp(resolved, tmp_real, tlen) == 0 && (resolved[tlen] == '/' || resolved[tlen] == '\0');
	if (!in_cwd && !in_tmp) {
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id,
			      "Cannot send: file is outside session directory and /tmp.", NULL);
		return;
	}

	struct stat st;
	if (stat(resolved, &st) != 0) {
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, "Cannot access file.", NULL);
		return;
	}
	if ((int64_t)st.st_size > CCGRAM_SEND_MAX_FILE_SIZE) {
		char msg[128];
		snprintf(msg, sizeof(msg), "File is too large (%lld MB). Limit is 50 MB.",
			 (long long)(st.st_size / (1024 * 1024)));
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, msg, NULL);
		return;
	}

	FILE *fp = fopen(resolved, "rb");
	if (!fp) {
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, "Cannot open file.", NULL);
		return;
	}
	uint8_t *buf = malloc((size_t)st.st_size + 1);
	if (!buf) {
		fclose(fp);
		return;
	}
	size_t nread = fread(buf, 1, (size_t)st.st_size, fp);
	fclose(fp);

	char *path_copy = xstrdup(resolved);
	const char *bname = basename(path_copy);
	struct tg_error err;
	bool ok = false;
	if (is_image_ext(resolved))
		ok = tg_send_photo_bytes(u->chat_id, u->thread_id, buf, nread, bname, NULL, NULL, &err);
	if (!ok)
		ok = tg_send_document_bytes(u->chat_id, u->thread_id, buf, nread, bname, NULL, NULL, NULL, &err);
	if (!ok) {
		char msg[320];
		snprintf(msg, sizeof(msg), "Failed to send file: %s", err.description);
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, msg, NULL);
	}
	free(path_copy);
	free(buf);
}

static const struct cmd_def COMMANDS[] = {
	{ "commands",   { "help", NULL },                   NULL,        "List commands",                cmd_commands },
	{ "screenshot", { "screen", NULL },                 NULL,        "Capture terminal",             cmd_screenshot_wrap },
	{ "live",       { NULL },                           NULL,        "Live terminal view",           cmd_live_wrap },
	{ "sessions",   { "ses", NULL },                    NULL,        "List sessions",                cmd_sessions },
	{ "new",        { NULL },                           "<key>",     "Create tmux session",          cmd_new },
	{ "bind",       { NULL },                           "<key>",     "Bind topic to session",        cmd_bind },
	{ "bindraw",    { NULL },                           "<session>", "Bind topic to exact tmux session", cmd_bindraw },
	{ "rename",     { NULL },                           "<name>",    "Rename topic and session",     cmd_rename },
	{ "unbind",     { NULL },                           NULL,        "Close topic, keep session",    cmd_unbind },
	{ "killme",     { NULL },                           NULL,        "Kill session and close topic", cmd_killme },
	{ "ctrl-c",     { "ctrlc", "ctrl_c", NULL },        NULL,        "Send Ctrl+C",                  cmd_ctrl_c },
	{ "ctrl-cc",    { "ctrlcc", "ctrl_cc", NULL },      NULL,        "Send Ctrl+C twice",            cmd_ctrl_cc },
	{ "enter",      { NULL },                           NULL,        "Send Enter",                   cmd_enter },
	{ "esc",        { "escape", NULL },                 NULL,        "Send Escape",                  cmd_esc },
	{ "tab",        { NULL },                           NULL,        "Send Tab",                     cmd_tab },
	{ "arrows",     { NULL },                           NULL,        "Toggle screen delta arrows",   cmd_arrows },
	{ "send",       { NULL },                           "<file>",    "Send file to Telegram",        cmd_send },
};

const struct cmd_def *handlers_get_commands(size_t *out_count)
{
	*out_count = sizeof(COMMANDS) / sizeof(COMMANDS[0]);
	return COMMANDS;
}

static void cmd_commands(const struct tg_update *u, const char *args)
{
	(void)args;
	struct strbuf sb;
	size_t n = sizeof(COMMANDS) / sizeof(COMMANDS[0]);

	sb_init(&sb);
	sb_appendf(&sb, "*Bot commands (prefix `%s`)*:\n\n", CCGRAM_COMMAND_PREFIX);
	for (size_t i = 0; i < n; i++) {
		const struct cmd_def *c = &COMMANDS[i];
		sb_appendf(&sb, "\xe2\x80\xa2 `%s%s", CCGRAM_COMMAND_PREFIX, c->name);
		if (c->params)
			sb_appendf(&sb, " %s", c->params);
		sb_append_str(&sb, "`");
		if (c->aliases[0])
			sb_appendf(&sb, " (or `%s%s`)", CCGRAM_COMMAND_PREFIX, c->aliases[0]);
		sb_appendf(&sb, " \xe2\x80\x94 %s\n", c->description);
	}
	sb_append_str(&sb, "\n_Commands and paths starting with `/` (for example `/bin/ls`) are sent directly to tmux._");
	tg_safe_reply(u->chat_id, u->has_thread_id ? u->thread_id : 0, u->message_id, sb.data, NULL);
	sb_free(&sb);
}

enum cmd_match_status match_command_token(const char *token, const struct cmd_def *cmds, size_t count,
					  const struct cmd_def **out_cmd,
					  const char **out_candidates, size_t max_cand, size_t *out_cand_count)
{
	size_t tlen = strlen(token);
	size_t matches = 0;
	size_t cand_n = 0;

	*out_cmd = NULL;
	if (out_cand_count)
		*out_cand_count = 0;
	if (tlen == 0)
		return CMD_MATCH_NONE;

	for (size_t i = 0; i < count; i++) {
		if (strcmp(token, cmds[i].name) == 0) {
			*out_cmd = &cmds[i];
			return CMD_MATCH_OK;
		}
		for (size_t a = 0; a < 4 && cmds[i].aliases[a]; a++) {
			if (strcmp(token, cmds[i].aliases[a]) == 0) {
				*out_cmd = &cmds[i];
				return CMD_MATCH_OK;
			}
		}
	}

	for (size_t i = 0; i < count; i++) {
		bool hit = strncmp(cmds[i].name, token, tlen) == 0;
		for (size_t a = 0; !hit && a < 4 && cmds[i].aliases[a]; a++) {
			if (strncmp(cmds[i].aliases[a], token, tlen) == 0)
				hit = true;
		}
		if (hit) {
			*out_cmd = &cmds[i];
			matches++;
			if (out_candidates && cand_n < max_cand)
				out_candidates[cand_n++] = cmds[i].name;
		}
	}

	if (matches == 0)
		return CMD_MATCH_NONE;
	if (matches == 1)
		return CMD_MATCH_OK;
	*out_cmd = NULL;
	if (out_cand_count)
		*out_cand_count = cand_n;
	return CMD_MATCH_AMBIGUOUS;
}

/* ── File Upload & Sanitization ────────────────────────────────────── */

void sanitize_filename(const char *in, char *out, size_t cap)
{
	const char *base = strrchr(in ? in : "", '/');
	const char *src = base ? base + 1 : (in ? in : "unnamed");
	size_t j = 0;
	bool only_dots = true;

	for (size_t i = 0; src[i] && j + 1 < cap && j < 200; i++) {
		unsigned char c = (unsigned char)src[i];
		if (isalnum(c) || c == '.' || c == '_' || c == '-') {
			out[j++] = (char)c;
			if (c != '.')
				only_dots = false;
		} else {
			out[j++] = '_';
			only_dots = false;
		}
	}
	out[j] = '\0';
	if (j == 0 || only_dots)
		snprintf(out, cap, "unnamed");
}

void sanitize_caption(const char *in, char *out, size_t cap)
{
	size_t j = 0;

	if (!in) {
		if (cap > 0)
			out[0] = '\0';
		return;
	}
	for (size_t i = 0; in[i] && j + 1 < cap && j < 500; i++) {
		unsigned char c = (unsigned char)in[i];
		if (c == '\n' || c == '\r')
			out[j++] = ' ';
		else if (c < 0x20 && c != '\t')
			continue;
		else if (c == 0x7f)
			continue;
		else
			out[j++] = (char)c;
	}
	out[j] = '\0';
}

static void handle_upload(const struct tg_update *u, const char *file_id,
			  const char *filename, int64_t file_size, const char *label)
{
	if (!u->has_thread_id) {
		tg_safe_reply(u->chat_id, 0, u->message_id, "No session bound to this topic.", NULL);
		return;
	}
	char wid[192];
	if (!resolve_topic_window(u->user_id, u->chat_id, u->thread_id, wid, sizeof(wid))) {
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, "No session bound to this topic.", NULL);
		return;
	}
	struct tmux_window w;
	if (!tmux_find_window_by_id(wid, &w) || !w.cwd[0]) {
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, "Session has no working directory.", NULL);
		return;
	}

	int64_t max_bytes = (int64_t)g_cfg.file_size_limit_mb * 1024LL * 1024LL;
	if (file_size > 0 && file_size > max_bytes) {
		char msg[128];
		snprintf(msg, sizeof(msg), "%s too large (%.1f MB). Maximum %d MB.",
			 label, (double)file_size / (1024.0 * 1024.0), g_cfg.file_size_limit_mb);
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, msg, NULL);
		return;
	}

	tg_send_chat_action(u->chat_id, u->thread_id, "typing");

	char upload_dir[600];
	snprintf(upload_dir, sizeof(upload_dir), "%s/%s", w.cwd, CCGRAM_UPLOAD_DIR);
	if (!mkdir_p(upload_dir, 0755) || path_is_symlink(upload_dir)) {
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, "Upload directory is invalid.", NULL);
		return;
	}

	char dest[768];
	snprintf(dest, sizeof(dest), "%s/%s", upload_dir, filename);
	if (access(dest, F_OK) == 0) {
		for (int i = 1; i < 100; i++) {
			snprintf(dest, sizeof(dest), "%s/%d_%s", upload_dir, i, filename);
			if (access(dest, F_OK) != 0)
				break;
		}
	}

	struct tg_error err;
	if (!tg_download_file(file_id, dest, &err)) {
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, "Failed to save file.", NULL);
		return;
	}

	char cap[512], tmux_in[1300], serr[256] = {0};
	sanitize_caption(u->caption, cap, sizeof(cap));
	char *ctrim = cap;
	while (*ctrim && isspace((unsigned char)*ctrim)) ctrim++;
	if (*ctrim)
		snprintf(tmux_in, sizeof(tmux_in), "%s %s", dest, ctrim);
	else
		snprintf(tmux_in, sizeof(tmux_in), "%s", dest);

	if (tmux_send_to_window(wid, tmux_in, serr, sizeof(serr))) {
		char *dcopy = xstrdup(dest);
		char msg[320];
		snprintf(msg, sizeof(msg), "Uploaded `%s/%s`", CCGRAM_UPLOAD_DIR, basename(dcopy));
		free(dcopy);
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, msg, NULL);
	} else {
		char msg[320];
		snprintf(msg, sizeof(msg), "File saved, failed to send path to tmux: %s", serr);
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, msg, NULL);
	}
}

/* ── Update Dispatcher ─────────────────────────────────────────────── */

static void handle_text_message(const struct tg_update *u)
{
	const char *text = u->text;
	char wid[192] = {0};

	if (u->has_thread_id && router_get_window_for_thread(u->user_id, u->thread_id, wid, sizeof(wid)))
		diff_mark_activity(u->chat_id, u->thread_id, wid, u->message_id);

	if (u->has_thread_id)
		router_set_group_chat_id(u->user_id, u->thread_id, u->chat_id);

	/* Check pending dashboard rename reply */
	char rename_wid[192];
	int64_t rename_tid = 0;
	if (user_state_pop_rename(u->user_id, u->has_thread_id ? u->thread_id : 0,
				  rename_wid, sizeof(rename_wid), &rename_tid)) {
		char raw[128];
		snprintf(raw, sizeof(raw), "%s", text);
		char *s = raw;
		while (*s && isspace((unsigned char)*s)) s++;
		size_t l = strlen(s);
		while (l > 0 && isspace((unsigned char)s[l - 1])) s[--l] = '\0';

		if (strcmp(s, "-") == 0 || strcmp(s, "/cancel") == 0 || strcmp(s, "cancel") == 0) {
			tg_safe_reply(u->chat_id, u->has_thread_id ? u->thread_id : 0, u->message_id, "Rename cancelled.", NULL);
			return;
		}
		if (!valid_name(s)) {
			tg_safe_reply(u->chat_id, u->has_thread_id ? u->thread_id : 0, u->message_id,
				      "Invalid session name. Must be 1-50 characters without newlines.", NULL);
			return;
		}
		char old_sess[128], target[128], clean[128];
		extract_session_name(rename_wid, old_sess, sizeof(old_sess));
		tmux_topic_name_from_session(s, clean, sizeof(clean));
		tmux_topic_session_name(clean, target, sizeof(target));
		if (!tmux_rename_session(old_sess, target)) {
			char msg[256];
			snprintf(msg, sizeof(msg), "Cannot rename session to `%s`.", s);
			tg_safe_reply(u->chat_id, u->has_thread_id ? u->thread_id : 0, u->message_id, msg, NULL);
			return;
		}
		if (rename_tid > 0 && u->has_thread_id && rename_tid == u->thread_id) {
			tmux_set_session_topic(target, u->chat_id, u->thread_id);
			struct tmux_window sess;
			if (find_topic_session(u->chat_id, u->thread_id, &sess))
				bind_runtime(u->user_id, u->chat_id, u->thread_id, &sess);
			tg_edit_forum_topic(u->chat_id, u->thread_id, clean, NULL);
		}
		char msg[256];
		snprintf(msg, sizeof(msg), "Renamed session to `%s`", s);
		tg_safe_reply(u->chat_id, u->has_thread_id ? u->thread_id : 0, u->message_id, msg, NULL);
		return;
	}

	if (!u->has_thread_id) {
		tg_safe_reply(u->chat_id, 0, u->message_id, "Use a named topic.", NULL);
		return;
	}

	if (!resolve_topic_window(u->user_id, u->chat_id, u->thread_id, wid, sizeof(wid))) {
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, "Topic is not bound. Use `//bind <key>`.", NULL);
		return;
	}

	/* Sidecar command: !!<cmd> */
	if (strncmp(text, "!!", 2) == 0) {
		const char *cmd = text + 2;
		while (*cmd && isspace((unsigned char)*cmd))
			cmd++;
		if (!*cmd) {
			tg_safe_reply(u->chat_id, u->thread_id, u->message_id, "Usage: `!!<command>`", NULL);
			return;
		}
		char existing[32];
		if (tmux_get_sidecar_pane_id(wid, existing, sizeof(existing))) {
			tg_safe_reply(u->chat_id, u->thread_id, u->message_id, "A sidecar command is already running.", NULL);
			return;
		}
		char spane[32], marker[64];
		if (!tmux_start_sidecar_command(wid, cmd, spane, sizeof(spane), marker, sizeof(marker))) {
			tg_safe_reply(u->chat_id, u->thread_id, u->message_id, "Failed to create or access sidecar pane.", NULL);
			return;
		}
		char ts[16], title[256];
		time_t t = time(NULL);
		struct tm tm;
		localtime_r(&t, &tm);
		strftime(ts, sizeof(ts), "%H:%M:%S", &tm);
		snprintf(title, sizeof(title), "Sidecar: %s (%s)", cmd, ts);
		diff_start_sidecar(u->chat_id, u->thread_id, wid, title);
		return;
	}

	tg_send_chat_action(u->chat_id, u->thread_id, "typing");
	char serr[256] = {0};
	if (!tmux_send_to_window(wid, text, serr, sizeof(serr))) {
		if (strncmp(serr, "Window not found", 16) == 0) {
			handlers_reconcile(false);
			char retry_wid[192] = {0};
			if (router_get_window_for_thread(u->user_id, u->thread_id, retry_wid, sizeof(retry_wid)) &&
			    strcmp(retry_wid, wid) != 0) {
				if (tmux_send_to_window(retry_wid, text, serr, sizeof(serr)))
					return;
			}
		}
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, serr, NULL);
	}
}

void handlers_dispatch_update(const struct tg_update *u)
{
	if (g_cfg.has_group_id && u->chat_id != 0 && u->chat_id != g_cfg.group_id)
		return;

	if (u->is_callback) {
		if (!config_is_user_allowed(u->user_id)) {
			tg_answer_callback(u->callback_id, "Not authorized", true);
			return;
		}
		if (strncmp(u->callback_data, CB_SCREEN_DIFF, strlen(CB_SCREEN_DIFF)) == 0)
			diff_handle_callback(u);
		else if (strncmp(u->callback_data, "sess:", 5) == 0)
			handle_sessions_callback(u);
		else
			live_handle_callback(u, false);
		return;
	}

	if (!config_is_user_allowed(u->user_id)) {
		if (u->text || u->has_photo || u->has_document)
			tg_safe_reply(u->chat_id, u->has_thread_id ? u->thread_id : 0,
				      u->message_id, "You are not authorized to use this bot.", NULL);
		return;
	}

	if (u->has_thread_id && u->message_id > 0)
		topic_tail_record(u->chat_id, u->thread_id, u->message_id);

	if (u->is_topic_created && u->has_thread_id && u->topic_created_name[0]) {
		char wid[192], err[256] = {0}, target[128];
		if (!ensure_topic_session(u->user_id, u->chat_id, u->thread_id, u->topic_created_name,
					  true, wid, sizeof(wid), err, sizeof(err))) {
			tg_rate_limit_send_message(u->chat_id, u->thread_id, err, NULL, CCGRAM_TG_SPLIT_LEN);
			return;
		}
		extract_session_name(wid, target, sizeof(target));
		tmux_switch_client(target);
		return;
	}

	if (u->is_topic_closed && u->has_thread_id) {
		struct tmux_window s;
		if (find_topic_session(u->chat_id, u->thread_id, &s) &&
		    tmux_kill_session(s.window_name)) {
			clear_topic_state(u->user_id, u->thread_id, s.window_id);
			router_unbind_thread(u->user_id, u->thread_id, NULL, 0);
		}
		return;
	}

	if (u->is_topic_edited && u->has_thread_id && u->topic_edited_name[0]) {
		struct tmux_window s;
		if (!find_topic_session(u->chat_id, u->thread_id, &s))
			return;
		char name[128], target[128];
		tmux_topic_name_from_session(u->topic_edited_name, name, sizeof(name));
		if (!valid_name(name))
			return;
		tmux_topic_session_name(name, target, sizeof(target));
		if (strcmp(s.window_name, target) != 0) {
			if (!tmux_rename_session(s.window_name, target))
				return;
			snprintf(s.window_name, sizeof(s.window_name), "%s", target);
			const char *colon = strrchr(s.window_id, ':');
			if (colon) {
				char bare[64];
				snprintf(bare, sizeof(bare), "%s", colon + 1);
				snprintf(s.window_id, sizeof(s.window_id), "%s:%s", target, bare);
			}
			tmux_set_session_topic(target, u->chat_id, u->thread_id);
			bind_runtime(u->user_id, u->chat_id, u->thread_id, &s);
		}
		return;
	}

	if (u->has_photo) {
		char fname[128];
		time_t t = time(NULL);
		struct tm tm;
		gmtime_r(&t, &tm);
		char tbuf[32];
		strftime(tbuf, sizeof(tbuf), "%Y%m%d_%H%M%S", &tm);
		snprintf(fname, sizeof(fname), "photo_%s_%.8s.jpg", tbuf, u->photo_unique_id);
		handle_upload(u, u->photo_file_id, fname, u->photo_file_size, "Photo");
		return;
	}

	if (u->has_document) {
		char fname[256];
		sanitize_filename(u->doc_file_name[0] ? u->doc_file_name : "document", fname, sizeof(fname));
		handle_upload(u, u->doc_file_id, fname, u->doc_file_size, "File");
		return;
	}

	if (!u->text)
		return;

	if (u->has_thread_id &&
	    (strcmp(u->chat_type, "group") == 0 || strcmp(u->chat_type, "supergroup") == 0)) {
		router_set_group_chat_id(u->user_id, u->thread_id, u->chat_id);
	}

	/* Check // prefix command */
	const char *p = u->text;
	while (*p && isspace((unsigned char)*p))
		p++;
	if (strncmp(p, CCGRAM_COMMAND_PREFIX, strlen(CCGRAM_COMMAND_PREFIX)) == 0) {
		const char *body = p + strlen(CCGRAM_COMMAND_PREFIX);
		while (*body && isspace((unsigned char)*body))
			body++;
		if (!*body) {
			tg_safe_reply(u->chat_id, u->has_thread_id ? u->thread_id : 0, u->message_id,
				      "Empty command. Use `//commands` for help.", NULL);
			return;
		}
		char token[64];
		size_t tlen = 0;
		while (body[tlen] && !isspace((unsigned char)body[tlen]) && tlen + 1 < sizeof(token)) {
			token[tlen] = body[tlen];
			tlen++;
		}
		token[tlen] = '\0';
		const char *args = body + tlen;
		while (*args && isspace((unsigned char)*args))
			args++;

		const struct cmd_def *cmd = NULL;
		const char *cands[8];
		size_t ncand = 0;
		enum cmd_match_status st = match_command_token(token, COMMANDS,
								 sizeof(COMMANDS) / sizeof(COMMANDS[0]),
								 &cmd, cands, 8, &ncand);
		if (st == CMD_MATCH_OK && cmd) {
			cmd->fn(u, args);
		} else if (st == CMD_MATCH_AMBIGUOUS) {
			struct strbuf sb;
			sb_init(&sb);
			sb_appendf(&sb, "Ambiguous command `//%s`. Possible matches: ", token);
			for (size_t i = 0; i < ncand; i++) {
				if (i > 0)
					sb_append_str(&sb, ", ");
				sb_appendf(&sb, "`//%s`", cands[i]);
			}
			tg_safe_reply(u->chat_id, u->has_thread_id ? u->thread_id : 0, u->message_id, sb.data, NULL);
			sb_free(&sb);
		} else {
			char msg[256];
			snprintf(msg, sizeof(msg),
				 "Unknown command `//%s`. Use `//commands` for the list of commands.", token);
			tg_safe_reply(u->chat_id, u->has_thread_id ? u->thread_id : 0, u->message_id, msg, NULL);
		}
		return;
	}

	handle_text_message(u);
}

/* ── Background Polling & Reconciliation ───────────────────────────── */

void handlers_reconcile(bool verify)
{
	struct tmux_window sessions[128];
	size_t ns = tmux_list_sessions(sessions, 128);
	int64_t def_user = g_cfg.allowed_user_count > 0 ? g_cfg.allowed_users[0] : 0;

	if (def_user == 0)
		return;

	for (size_t i = 0; i < ns; i++) {
		if (!sessions[i].has_topic_ref)
			continue;
		int64_t cid = sessions[i].topic_chat_id;
		int64_t tid = sessions[i].topic_thread_id;

		/* Skip duplicates */
		bool dup = false;
		for (size_t j = 0; j < ns; j++) {
			if (i != j && sessions[j].has_topic_ref &&
			    sessions[j].topic_chat_id == cid &&
			    sessions[j].topic_thread_id == tid) {
				dup = true;
				break;
			}
		}
		if (dup)
			continue;

		char cur_wid[192] = {0};
		bool changed = !router_get_window_for_thread(def_user, tid, cur_wid, sizeof(cur_wid)) ||
			       strcmp(cur_wid, sessions[i].window_id) != 0;
		if (changed) {
			bind_runtime(def_user, cid, tid, &sessions[i]);
			char *pane = tmux_capture_pane(sessions[i].window_id, true, true);
			if (pane) {
				diff_prime(cid, tid, sessions[i].window_id, pane, "main");
				free(pane);
			}
		}

		if (changed || verify) {
			struct tg_error err;
			if (!tg_reopen_forum_topic(cid, tid, &err) && tg_is_thread_gone(&err)) {
				if (tmux_clear_session_topic(sessions[i].window_name)) {
					clear_topic_state(def_user, tid, sessions[i].window_id);
					router_unbind_thread(def_user, tid, NULL, 0);
				}
			}
		}
	}

	/* Clear orphans */
	struct thread_binding bindings[256];
	size_t nb = router_iter_bindings(bindings, 256);
	for (size_t i = 0; i < nb; i++) {
		int64_t cid = router_resolve_chat_id(bindings[i].user_id, bindings[i].thread_id);
		bool found = false;
		for (size_t j = 0; j < ns; j++) {
			if (sessions[j].has_topic_ref &&
			    sessions[j].topic_chat_id == cid &&
			    sessions[j].topic_thread_id == bindings[i].thread_id) {
				found = true;
				break;
			}
		}
		if (!found) {
			clear_topic_state(bindings[i].user_id, bindings[i].thread_id, bindings[i].window_id);
			router_unbind_thread(bindings[i].user_id, bindings[i].thread_id, NULL, 0);
		}
	}
}

static void poll_sidecar(int64_t chat_id, int64_t thread_id, const char *wid)
{
	char spane[32], marker[64];

	if (!tmux_get_sidecar_pane_id(wid, spane, sizeof(spane)))
		return;
	char *raw = tmux_capture_pane_by_id(spane, false, true, wid);
	if (!raw) {
		tmux_forget_sidecar_pane(wid);
		return;
	}
	if (!tmux_get_sidecar_marker(wid, marker, sizeof(marker))) {
		free(raw);
		tmux_forget_sidecar_pane(wid);
		return;
	}
	char *out = NULL;
	bool completed = false;
	bool has_begin = extract_sidecar_output(raw, marker, &out, &completed);
	free(raw);
	if (!has_begin) {
		free(out);
		return;
	}
	if (g_cfg.topic_status_diff_enabled)
		diff_update(chat_id, thread_id, wid, (out && *out) ? out : "(no output)", "sidecar", completed);
	free(out);
	if (completed && tmux_kill_pane(spane, wid))
		tmux_forget_sidecar_pane(wid);
}

void *polling_thread_main(void *arg)
{
	(void)arg;
	double last_topic_check = 0.0;
	double last_live_view = 0.0;

	log_info("Status polling started (interval: %.1fs)", g_cfg.status_poll_interval);
	while (g_running) {
		double now = mono_now();
		bool verify = (now - last_topic_check) >= CCGRAM_TOPIC_CHECK_INTERVAL;

		handlers_reconcile(verify);
		if (verify) {
			last_topic_check = now;
			log_throttle_sweep();
		}

		if ((now - last_live_view) >= (double)g_cfg.live_view_interval) {
			last_live_view = now;
			live_tick_all();
		}

		struct tmux_window sessions[128];
		size_t ns = tmux_list_sessions(sessions, 128);
		struct thread_binding bindings[256];
		size_t nb = router_iter_bindings(bindings, 256);

		for (size_t i = 0; i < nb && g_running; i++) {
			bool dup_win = false;
			for (size_t prev = 0; prev < i; prev++) {
				if (strcmp(bindings[prev].window_id, bindings[i].window_id) == 0) {
					dup_win = true;
					break;
				}
			}
			if (dup_win)
				continue;

			const struct tmux_window *w = NULL;
			for (size_t j = 0; j < ns; j++) {
				if (strcmp(sessions[j].window_id, bindings[i].window_id) == 0) {
					w = &sessions[j];
					break;
				}
			}
			if (!w)
				continue;

			int64_t chat_id = router_resolve_chat_id(bindings[i].user_id, bindings[i].thread_id);
			char *pane = tmux_capture_pane(w->window_id, true, true);
			if (pane) {
				if (g_cfg.topic_status_diff_enabled)
					diff_update(chat_id, bindings[i].thread_id, bindings[i].window_id,
						    pane, "main", false);
				free(pane);
			}
			poll_sidecar(chat_id, bindings[i].thread_id, bindings[i].window_id);
		}

		struct timespec ts = {
			.tv_sec = (time_t)g_cfg.status_poll_interval,
			.tv_nsec = (long)((g_cfg.status_poll_interval - (double)(time_t)g_cfg.status_poll_interval) * 1e9)
		};
		nanosleep(&ts, NULL);
	}
	return NULL;
}
