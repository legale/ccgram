#include "ccgram.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_DIFF_STATES 256

struct diff_state {
	int64_t chat_id;
	int64_t thread_id;
	char target[16];
	char window_id[192];
	int64_t message_id;
	double last_edit_ts;
	uint64_t last_msg_ts;
	uint64_t diff_ts;
	char title[256];
	char *last_display_text;
	bool send_suppressed;
	double last_change_ts;
	double last_typing_ts;
	bool arrows_enabled;
};

static pthread_mutex_t g_diff_mu = PTHREAD_MUTEX_INITIALIZER;
static struct diff_state g_diffs[MAX_DIFF_STATES];
static size_t g_diff_count = 0;

void diff_init(void)
{
	diff_reset();
}

void diff_reset(void)
{
	pthread_mutex_lock(&g_diff_mu);
	for (size_t i = 0; i < g_diff_count; i++)
		free(g_diffs[i].last_display_text);
	memset(g_diffs, 0, sizeof(g_diffs));
	g_diff_count = 0;
	pthread_mutex_unlock(&g_diff_mu);
}

static char *normalize_screen_text(const char *pane_text)
{
	struct strbuf sb;
	const char *p = pane_text ? pane_text : "";

	sb_init(&sb);
	while (*p) {
		/* Strip ANSI CSI: \x1b[...[@-~] */
		if (p[0] == '\x1b' && p[1] == '[') {
			p += 2;
			while (*p && (unsigned char)*p >= 0x20 && (unsigned char)*p <= 0x3f)
				p++;
			if (*p && (unsigned char)*p >= 0x40 && (unsigned char)*p <= 0x7e)
				p++;
			continue;
		}
		/* Strip OSC: \x1b]...(\x07|\x1b\\) */
		if (p[0] == '\x1b' && p[1] == ']') {
			p += 2;
			while (*p && *p != '\x07' && !(p[0] == '\x1b' && p[1] == '\\'))
				p++;
			if (*p == '\x07')
				p++;
			else if (p[0] == '\x1b' && p[1] == '\\')
				p += 2;
			continue;
		}
		/* Strip 2-byte ESC */
		if (p[0] == '\x1b' && (unsigned char)p[1] >= 0x40 && (unsigned char)p[1] <= 0x5f) {
			p += 2;
			continue;
		}
		if (p[0] == '\r' && p[1] == '\n') {
			sb_append(&sb, "\n", 1);
			p += 2;
			continue;
		}
		if (p[0] == '\r') {
			sb_append(&sb, "\n", 1);
			p++;
			continue;
		}
		if (p[0] == '\t') {
			sb_append_str(&sb, "    ");
			p++;
			continue;
		}
		/* Strip control chars except \n */
		if ((unsigned char)*p < 0x20 && *p != '\n') {
			p++;
			continue;
		}
		if ((unsigned char)*p == 0x7f) {
			p++;
			continue;
		}
		/* Collapse long repeats of '_' or '─' (UTF-8 e2 94 80) to 30 */
		if (*p == '_') {
			size_t run = 0;
			while (p[run] == '_')
				run++;
			if (run > 30) {
				for (int k = 0; k < 30; k++)
					sb_append(&sb, "_", 1);
				p += run;
				continue;
			}
		}
		if ((uint8_t)p[0] == 0xe2 && (uint8_t)p[1] == 0x94 && (uint8_t)p[2] == 0x80) {
			size_t run = 0;
			while ((uint8_t)p[run * 3] == 0xe2 && (uint8_t)p[run * 3 + 1] == 0x94 &&
			       (uint8_t)p[run * 3 + 2] == 0x80)
				run++;
			if (run > 30) {
				for (int k = 0; k < 30; k++)
					sb_append(&sb, "\xe2\x94\x80", 3);
				p += run * 3;
				continue;
			}
		}

		sb_append(&sb, p, 1);
		p++;
	}
	return sb_detach(&sb);
}

char *diff_tail_lines(const char *text, int limit)
{
	int eff = limit > 0 ? limit : g_cfg.screen_diff_limit;
	char *norm = normalize_screen_text(text);
	size_t nlines = 0;
	size_t cap = 64;
	const char **lines = calloc(cap, sizeof(char *));
	char *p = norm;
	struct strbuf out;

	if (!lines) {
		free(norm);
		return xstrdup("");
	}

	while (*p) {
		char *nl = strchr(p, '\n');
		if (nlines == cap) {
			cap *= 2;
			const char **nl_arr = realloc(lines, cap * sizeof(char *));
			if (!nl_arr)
				break;
			lines = nl_arr;
		}
		lines[nlines++] = p;
		if (!nl)
			break;
		*nl = '\0';
		p = nl + 1;
	}

	size_t start = nlines;
	int used = 0;
	for (size_t i = nlines; i > 0; i--) {
		int cost = (int)strlen(lines[i - 1]) + 1;
		if (used + cost > eff)
			break;
		used += cost;
		start = i - 1;
	}

	sb_init(&out);
	for (size_t i = start; i < nlines; i++) {
		if (i > start)
			sb_append(&out, "\n", 1);
		sb_append_str(&out, lines[i]);
	}

	free(lines);
	free(norm);
	return sb_detach(&out);
}

static void format_limit_label(int limit, char *out, size_t cap)
{
	if (limit >= 1000 && limit % 1000 == 0)
		snprintf(out, cap, "%dk", limit / 1000);
	else
		snprintf(out, cap, "%d", limit);
}

static void diff_build_keyboard_with_limit(struct tg_keyboard *kb, bool arrows, int limit)
{
	char buf_lbl[16];

	memset(kb, 0, sizeof(*kb));
	format_limit_label(limit, buf_lbl, sizeof(buf_lbl));

	kb->num_rows = arrows ? 2 : 1;
	kb->row_lens[0] = 5;

	snprintf(kb->rows[0][0].text, 96, "%s", buf_lbl);
	snprintf(kb->rows[0][0].callback_data, CCGRAM_MAX_CB_DATA + 1, "%sbuf", CB_SCREEN_DIFF);

	snprintf(kb->rows[0][1].text, 96, "Ent");
	snprintf(kb->rows[0][1].callback_data, CCGRAM_MAX_CB_DATA + 1, "%sent", CB_SCREEN_DIFF);

	snprintf(kb->rows[0][2].text, 96, "Esc");
	snprintf(kb->rows[0][2].callback_data, CCGRAM_MAX_CB_DATA + 1, "%sesc", CB_SCREEN_DIFF);

	snprintf(kb->rows[0][3].text, 96, "%s", arrows ? "Tab" : "\xe2\x86\x92");
	snprintf(kb->rows[0][3].callback_data, CCGRAM_MAX_CB_DATA + 1, "%s%s",
		 CB_SCREEN_DIFF, arrows ? "tab" : "right");

	snprintf(kb->rows[0][4].text, 96, "Ar");
	snprintf(kb->rows[0][4].callback_data, CCGRAM_MAX_CB_DATA + 1, "%sarrows", CB_SCREEN_DIFF);

	if (arrows) {
		kb->row_lens[1] = 4;
		snprintf(kb->rows[1][0].text, 96, "\xe2\x86\x91");
		snprintf(kb->rows[1][0].callback_data, CCGRAM_MAX_CB_DATA + 1, "%sup", CB_SCREEN_DIFF);
		snprintf(kb->rows[1][1].text, 96, "\xe2\x86\x90");
		snprintf(kb->rows[1][1].callback_data, CCGRAM_MAX_CB_DATA + 1, "%sleft", CB_SCREEN_DIFF);
		snprintf(kb->rows[1][2].text, 96, "\xe2\x86\x93");
		snprintf(kb->rows[1][2].callback_data, CCGRAM_MAX_CB_DATA + 1, "%sdown", CB_SCREEN_DIFF);
		snprintf(kb->rows[1][3].text, 96, "\xe2\x86\x92");
		snprintf(kb->rows[1][3].callback_data, CCGRAM_MAX_CB_DATA + 1, "%sright", CB_SCREEN_DIFF);
	}
}

void diff_build_keyboard(struct tg_keyboard *kb, bool arrows)
{
	int limit;

	pthread_mutex_lock(&g_diff_mu);
	limit = g_cfg.screen_diff_limit;
	pthread_mutex_unlock(&g_diff_mu);
	diff_build_keyboard_with_limit(kb, arrows, limit);
}

static char *format_screen(const char *window_id, const char *pane_text, const char *title, int limit)
{
	char *body = diff_tail_lines(pane_text, limit);
	struct strbuf sb;

	sb_init(&sb);
	if (title && *title)
		sb_appendf(&sb, "%s\n```\n%s\n```", title, body);
	else
		sb_appendf(&sb, "Screen delta %s\n```\n%s\n```", window_id, body);
	free(body);
	return sb_detach(&sb);
}

static struct diff_state *get_state_locked(int64_t chat_id, int64_t thread_id,
					   const char *window_id, const char *target)
{
	for (size_t i = 0; i < g_diff_count; i++) {
		if (g_diffs[i].chat_id == chat_id && g_diffs[i].thread_id == thread_id &&
		    strcmp(g_diffs[i].target, target) == 0) {
			if (strcmp(g_diffs[i].window_id, window_id) != 0) {
				free(g_diffs[i].last_display_text);
				memset(&g_diffs[i], 0, sizeof(g_diffs[i]));
				g_diffs[i].chat_id = chat_id;
				g_diffs[i].thread_id = thread_id;
				snprintf(g_diffs[i].target, sizeof(g_diffs[i].target), "%s", target);
				snprintf(g_diffs[i].window_id, sizeof(g_diffs[i].window_id), "%s", window_id);
				g_diffs[i].arrows_enabled = g_cfg.screen_diff_arrows;
			}
			return &g_diffs[i];
		}
	}
	if (g_diff_count >= MAX_DIFF_STATES)
		return NULL;
	struct diff_state *s = &g_diffs[g_diff_count++];
	memset(s, 0, sizeof(*s));
	s->chat_id = chat_id;
	s->thread_id = thread_id;
	snprintf(s->target, sizeof(s->target), "%s", target);
	snprintf(s->window_id, sizeof(s->window_id), "%s", window_id);
	s->arrows_enabled = g_cfg.screen_diff_arrows;
	return s;
}

void diff_start_sidecar(int64_t chat_id, int64_t thread_id, const char *window_id, const char *title)
{
	pthread_mutex_lock(&g_diff_mu);
	struct diff_state *s = get_state_locked(chat_id, thread_id, window_id, "sidecar");
	if (s) {
		s->message_id = 0;
		snprintf(s->title, sizeof(s->title), "%s", title ? title : "");
		free(s->last_display_text);
		s->last_display_text = NULL;
		s->last_edit_ts = 0.0;
		s->last_msg_ts = time_ns_now();
		s->diff_ts = 0;
		s->send_suppressed = false;
	}
	pthread_mutex_unlock(&g_diff_mu);
}

void diff_mark_activity(int64_t chat_id, int64_t thread_id, const char *window_id, int64_t message_id)
{
	(void)window_id;
	if (message_id <= 0)
		return;
	topic_tail_record(chat_id, thread_id, message_id);
	uint64_t now = time_ns_now();
	double now_mono = mono_now();

	pthread_mutex_lock(&g_diff_mu);
	for (size_t i = 0; i < g_diff_count; i++) {
		if (g_diffs[i].chat_id == chat_id && g_diffs[i].thread_id == thread_id) {
			uint64_t next = g_diffs[i].last_msg_ts + 1;
			g_diffs[i].last_msg_ts = now > next ? now : next;
			g_diffs[i].last_change_ts = now_mono;
			g_diffs[i].send_suppressed = false;
		}
	}
	pthread_mutex_unlock(&g_diff_mu);
}

void diff_prime(int64_t chat_id, int64_t thread_id, const char *window_id,
		const char *pane_text, const char *target)
{
	if (!pane_text || !*pane_text)
		return;
	pthread_mutex_lock(&g_diff_mu);
	struct diff_state *s = get_state_locked(chat_id, thread_id, window_id, target ? target : "main");
	if (s) {
		free(s->last_display_text);
		s->last_display_text = format_screen(window_id, pane_text, s->title[0] ? s->title : NULL,
						     g_cfg.screen_diff_limit);
		s->last_edit_ts = mono_now();
	}
	pthread_mutex_unlock(&g_diff_mu);
}

void diff_clear_topic(int64_t thread_id)
{
	pthread_mutex_lock(&g_diff_mu);
	for (size_t i = 0; i < g_diff_count;) {
		if (g_diffs[i].thread_id == thread_id) {
			free(g_diffs[i].last_display_text);
			g_diffs[i] = g_diffs[--g_diff_count];
		} else {
			i++;
		}
	}
	pthread_mutex_unlock(&g_diff_mu);
}

void diff_update(int64_t chat_id, int64_t thread_id, const char *window_id,
		 const char *pane_text, const char *target, bool force)
{
	if (!g_cfg.topic_status_diff_enabled || !pane_text || !*pane_text)
		return;

	double now = mono_now();
	bool send_typing = false;
	bool should_send_or_edit = false;
	bool do_send_new = false;
	int64_t cur_msg_id = 0, prev_msg_id = 0;
	uint64_t snap_msg_ts = 0;
	bool arrows = false;
	char *text = NULL;

	pthread_mutex_lock(&g_diff_mu);
	struct diff_state *s = get_state_locked(chat_id, thread_id, window_id, target ? target : "main");
	if (!s) {
		pthread_mutex_unlock(&g_diff_mu);
		return;
	}
	int snap_limit = g_cfg.screen_diff_limit;
	text = format_screen(window_id, pane_text, s->title[0] ? s->title : NULL, snap_limit);
	if (!s->last_display_text || strcmp(text, s->last_display_text) != 0)
		s->last_change_ts = now;

	if ((now - s->last_change_ts) <= 5.0 && (now - s->last_typing_ts) >= 4.0) {
		s->last_typing_ts = now;
		send_typing = true;
	}

	if ((!s->last_display_text || strcmp(text, s->last_display_text) != 0) &&
	    (force || (now - s->last_edit_ts) >= (double)g_cfg.topic_status_diff_interval) &&
	    !s->send_suppressed) {
		should_send_or_edit = true;
		snap_msg_ts = s->last_msg_ts;
		cur_msg_id = s->message_id;
		prev_msg_id = s->message_id;
		arrows = s->arrows_enabled;
		if (cur_msg_id <= 0 || s->diff_ts < snap_msg_ts ||
		    !topic_tail_is_last(chat_id, thread_id, cur_msg_id)) {
			do_send_new = true;
		}
	}
	pthread_mutex_unlock(&g_diff_mu);

	if (send_typing)
		tg_send_chat_action(chat_id, thread_id, "typing");

	if (!should_send_or_edit) {
		free(text);
		return;
	}

	struct tg_keyboard kb;
	diff_build_keyboard_with_limit(&kb, arrows, snap_limit);
	bool ok = false;
	int64_t new_msg_id = cur_msg_id;

	if (!do_send_new) {
		struct tg_error err;
		ok = tg_edit_with_fallback(chat_id, cur_msg_id, text, &kb, &err);
		if (ok) {
			pthread_mutex_lock(&g_diff_mu);
			s = get_state_locked(chat_id, thread_id, window_id, target ? target : "main");
			if (s && s->last_msg_ts != snap_msg_ts)
				do_send_new = true;
			pthread_mutex_unlock(&g_diff_mu);
		} else if (err.code == 429) {
			free(text);
			return;
		} else {
			do_send_new = true;
		}
	}

	if (do_send_new) {
		new_msg_id = tg_rate_limit_send_message(chat_id, thread_id, text, &kb, CCGRAM_TG_MAX_MSG);
		if (new_msg_id <= 0) {
			pthread_mutex_lock(&g_diff_mu);
			s = get_state_locked(chat_id, thread_id, window_id, target ? target : "main");
			if (s)
				s->send_suppressed = true;
			pthread_mutex_unlock(&g_diff_mu);
			free(text);
			return;
		}
		if (prev_msg_id > 0 && prev_msg_id != new_msg_id)
			tg_edit_message_reply_markup(chat_id, prev_msg_id, NULL, NULL);
		topic_tail_record(chat_id, thread_id, new_msg_id);
		ok = true;
	}

	if (ok) {
		pthread_mutex_lock(&g_diff_mu);
		s = get_state_locked(chat_id, thread_id, window_id, target ? target : "main");
		if (s) {
			s->message_id = new_msg_id;
			s->send_suppressed = false;
			s->diff_ts = snap_msg_ts;
			free(s->last_display_text);
			s->last_display_text = text;
			s->last_edit_ts = mono_now();
			text = NULL;
		}
		pthread_mutex_unlock(&g_diff_mu);
	}
	free(text);
}

int diff_toggle_arrows(int64_t chat_id, int64_t thread_id)
{
	int64_t msg_id = 0;
	bool enabled = false;

	pthread_mutex_lock(&g_diff_mu);
	for (size_t i = 0; i < g_diff_count; i++) {
		if (g_diffs[i].chat_id == chat_id && g_diffs[i].thread_id == thread_id &&
		    strcmp(g_diffs[i].target, "main") == 0 && g_diffs[i].message_id > 0) {
			g_diffs[i].arrows_enabled = !g_diffs[i].arrows_enabled;
			enabled = g_diffs[i].arrows_enabled;
			msg_id = g_diffs[i].message_id;
			break;
		}
	}
	pthread_mutex_unlock(&g_diff_mu);

	if (msg_id <= 0)
		return -1;

	struct tg_keyboard kb;
	diff_build_keyboard(&kb, enabled);
	if (!tg_edit_message_reply_markup(chat_id, msg_id, &kb, NULL))
		return -2;
	return enabled ? 1 : 0;
}

void diff_handle_callback(const struct tg_update *u)
{
	const char *data = u->callback_data;
	const char *action = data + strlen(CB_SCREEN_DIFF);

	if (strcmp(action, "arrows") == 0) {
		if (!u->has_thread_id) {
			tg_answer_callback(u->callback_id, "Use this button inside a topic", true);
			return;
		}
		int res = diff_toggle_arrows(u->chat_id, u->thread_id);
		if (res == -1)
			tg_answer_callback(u->callback_id, "No screen delta message in this topic", true);
		else if (res == -2)
			tg_answer_callback(u->callback_id, "Failed to update screen delta buttons", true);
		else
			tg_answer_callback(u->callback_id, res ? "arrows on" : "arrows off", false);
		return;
	}

	const char *key = NULL;
	if (strcmp(action, "up") == 0) key = "Up";
	else if (strcmp(action, "left") == 0) key = "Left";
	else if (strcmp(action, "down") == 0) key = "Down";
	else if (strcmp(action, "ent") == 0) key = "Enter";
	else if (strcmp(action, "esc") == 0) key = "Escape";
	else if (strcmp(action, "right") == 0) key = "Right";
	else if (strcmp(action, "tab") == 0) key = "Tab";

	if (key) {
		char wid[192];
		if (!u->has_thread_id) {
			tg_answer_callback(u->callback_id, "Use this button inside a topic", true);
			return;
		}
		if (!router_get_window_for_thread(u->user_id, u->thread_id, wid, sizeof(wid))) {
			tg_answer_callback(u->callback_id, "This topic is not bound to a tmux session", true);
			return;
		}
		if (tmux_send_keys(wid, key, false, false))
			tg_answer_callback(u->callback_id, key, false);
		else
			tg_answer_callback(u->callback_id, "Failed to send key", true);
		return;
	}

	if (strcmp(action, "buf") == 0) {
		int step = (int)(g_cfg.screen_diff_max_limit * g_cfg.screen_diff_step_percent + 0.5);
		int new_limit;
		if (step < g_cfg.screen_diff_min_limit)
			step = g_cfg.screen_diff_min_limit;

		pthread_mutex_lock(&g_diff_mu);
		int cur = g_cfg.screen_diff_limit;
		if (cur >= g_cfg.screen_diff_max_limit)
			new_limit = step;
		else {
			int nxt = cur + step;
			new_limit = nxt < g_cfg.screen_diff_max_limit ? nxt : g_cfg.screen_diff_max_limit;
		}
		g_cfg.screen_diff_limit = new_limit;
		for (size_t i = 0; i < g_diff_count; i++) {
			free(g_diffs[i].last_display_text);
			g_diffs[i].last_display_text = NULL;
			g_diffs[i].last_edit_ts = 0.0;
		}
		pthread_mutex_unlock(&g_diff_mu);

		char msg[64];
		snprintf(msg, sizeof(msg), "Screen buffer: %d", new_limit);
		tg_answer_callback(u->callback_id, msg, false);
		return;
	}

	tg_answer_callback(u->callback_id, "Unknown screen buffer action", true);
}
