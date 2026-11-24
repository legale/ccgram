#include "ccgram.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define MAX_LIVE_VIEWS 64

struct live_view_state {
	int64_t chat_id;
	int64_t message_id;
	int64_t thread_id;
	int64_t user_id;
	char window_id[192];
	char last_hash[33];
	double next_edit_after;
	double start_time;
};

static pthread_mutex_t g_live_mu = PTHREAD_MUTEX_INITIALIZER;
static struct live_view_state g_views[MAX_LIVE_VIEWS];
static size_t g_view_count = 0;

void live_init(void)
{
	pthread_mutex_lock(&g_live_mu);
	g_view_count = 0;
	pthread_mutex_unlock(&g_live_mu);
}

void live_clear_view(int64_t user_id, int64_t thread_id)
{
	pthread_mutex_lock(&g_live_mu);
	for (size_t i = 0; i < g_view_count; i++) {
		if (g_views[i].user_id == user_id && g_views[i].thread_id == thread_id) {
			g_views[i] = g_views[--g_view_count];
			break;
		}
	}
	pthread_mutex_unlock(&g_live_mu);
}

bool live_is_active(int64_t user_id, int64_t thread_id)
{
	bool active = false;

	pthread_mutex_lock(&g_live_mu);
	for (size_t i = 0; i < g_view_count; i++) {
		if (g_views[i].user_id == user_id && g_views[i].thread_id == thread_id) {
			active = true;
			break;
		}
	}
	pthread_mutex_unlock(&g_live_mu);
	return active;
}

static void start_live_view(const struct live_view_state *st)
{
	pthread_mutex_lock(&g_live_mu);
	for (size_t i = 0; i < g_view_count; i++) {
		if (g_views[i].user_id == st->user_id && g_views[i].thread_id == st->thread_id) {
			g_views[i] = *st;
			pthread_mutex_unlock(&g_live_mu);
			return;
		}
	}
	if (g_view_count < MAX_LIVE_VIEWS)
		g_views[g_view_count++] = *st;
	pthread_mutex_unlock(&g_live_mu);
}

static size_t visible_ansi_len(const char *line, size_t len)
{
	size_t vis = 0;
	size_t i = 0;

	while (i < len) {
		if (line[i] == '\x1b' && i + 1 < len) {
			if (line[i + 1] == '[') {
				i += 2;
				while (i < len && (unsigned char)line[i] >= 0x20 && (unsigned char)line[i] <= 0x3f)
					i++;
				if (i < len && (unsigned char)line[i] >= 0x40 && (unsigned char)line[i] <= 0x7e)
					i++;
				continue;
			}
			if (line[i + 1] == ']') {
				i += 2;
				while (i < len && line[i] != '\x07' && !(line[i] == '\x1b' && i + 1 < len && line[i + 1] == '\\'))
					i++;
				if (i < len && line[i] == '\x07')
					i++;
				else if (i + 1 < len && line[i] == '\x1b' && line[i + 1] == '\\')
					i += 2;
				continue;
			}
			if ((unsigned char)line[i + 1] >= 0x40 && (unsigned char)line[i + 1] <= 0x5f) {
				i += 2;
				continue;
			}
		}
		if ((line[i] & 0xc0) != 0x80)
			vis++;
		i++;
	}
	return vis;
}

char *live_tail_lines_ansi(const char *text, int limit)
{
	int eff = limit > 0 ? limit : g_cfg.live_view_limit;
	char *copy = xstrdup(text ? text : "");
	size_t cap = 64, nlines = 0;
	char **lines = calloc(cap, sizeof(char *));
	char *p = copy;
	struct strbuf out;

	if (!lines || !copy) {
		free(copy);
		free(lines);
		return xstrdup("");
	}

	while (*p) {
		char *nl = strchr(p, '\n');
		if (nlines == cap) {
			cap *= 2;
			char **nl_arr = realloc(lines, cap * sizeof(char *));
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
		int cost = (int)visible_ansi_len(lines[i - 1], strlen(lines[i - 1])) + 1;
		if (used + cost > eff && start < nlines)
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
	free(copy);
	return sb_detach(&out);
}

static void add_key_btn(struct tg_button *b, const char *label, const char *key_id, const char *window_id)
{
	snprintf(b->text, sizeof(b->text), "%s", label);
	snprintf(b->callback_data, sizeof(b->callback_data), "%s%s:%s", CB_KEYS_PREFIX, key_id, window_id);
}

static void fill_common_key_rows(struct tg_keyboard *kb, const char *window_id)
{
	kb->row_lens[0] = 3;
	add_key_btn(&kb->rows[0][0], "\xe2\x90\xa3 Space", "spc", window_id);
	add_key_btn(&kb->rows[0][1], "\xe2\x86\x91", "up", window_id);
	add_key_btn(&kb->rows[0][2], "\xe2\x87\xa5 Tab", "tab", window_id);

	kb->row_lens[1] = 3;
	add_key_btn(&kb->rows[1][0], "\xe2\x86\x90", "lt", window_id);
	add_key_btn(&kb->rows[1][1], "\xe2\x86\x93", "dn", window_id);
	add_key_btn(&kb->rows[1][2], "\xe2\x86\x92", "rt", window_id);

	kb->row_lens[2] = 3;
	add_key_btn(&kb->rows[2][0], "\xe2\x8e\x8b Esc", "esc", window_id);
	add_key_btn(&kb->rows[2][1], "^C", "cc", window_id);
	add_key_btn(&kb->rows[2][2], "\xe2\x8f\x8e Enter", "ent", window_id);
}

void live_build_screenshot_keyboard(struct tg_keyboard *kb, const char *window_id)
{
	memset(kb, 0, sizeof(*kb));
	kb->num_rows = 4;
	fill_common_key_rows(kb, window_id);

	kb->row_lens[3] = 2;
	snprintf(kb->rows[3][0].text, 96, "Live");
	snprintf(kb->rows[3][0].callback_data, CCGRAM_MAX_CB_DATA + 1, "%s%s", CB_LIVE_START, window_id);
	snprintf(kb->rows[3][1].text, 96, "Refresh");
	snprintf(kb->rows[3][1].callback_data, CCGRAM_MAX_CB_DATA + 1, "%s%s", CB_SCREENSHOT_REFRESH, window_id);
}

void live_build_keyboard(struct tg_keyboard *kb, const char *window_id)
{
	memset(kb, 0, sizeof(*kb));
	kb->num_rows = 4;
	fill_common_key_rows(kb, window_id);

	kb->row_lens[3] = 1;
	snprintf(kb->rows[3][0].text, 96, "Stop Live");
	snprintf(kb->rows[3][0].callback_data, CCGRAM_MAX_CB_DATA + 1, "%s%s", CB_LIVE_STOP, window_id);
}

static void format_hhmmss(char *out, size_t cap)
{
	time_t t = time(NULL);
	struct tm tm;
	localtime_r(&t, &tm);
	strftime(out, cap, "%H:%M:%S", &tm);
}

void live_tick_all(void)
{
	struct live_view_state snap[MAX_LIVE_VIEWS];
	size_t n;
	double now = mono_now();

	pthread_mutex_lock(&g_live_mu);
	n = g_view_count;
	if (n > 0)
		memcpy(snap, g_views, n * sizeof(struct live_view_state));
	pthread_mutex_unlock(&g_live_mu);

	for (size_t i = 0; i < n; i++) {
		struct live_view_state *v = &snap[i];
		struct tg_keyboard kb;

		if (now - v->start_time > (double)g_cfg.live_view_timeout) {
			live_clear_view(v->user_id, v->thread_id);
			live_build_screenshot_keyboard(&kb, v->window_id);
			tg_edit_message_caption(v->chat_id, v->message_id, "Live view ended (timeout)", &kb, NULL);
			continue;
		}
		if (now < v->next_edit_after)
			continue;

		struct tmux_window w;
		if (!tmux_find_window_by_id(v->window_id, &w)) {
			live_clear_view(v->user_id, v->thread_id);
			live_build_screenshot_keyboard(&kb, v->window_id);
			tg_edit_message_caption(v->chat_id, v->message_id, "Live view ended (window closed)", &kb, NULL);
			continue;
		}

		char *raw = tmux_capture_pane(w.window_id, true, false);
		if (!raw)
			continue;
		char *text = live_tail_lines_ansi(raw, g_cfg.live_view_limit);
		free(raw);

		char h[33];
		content_hash_hex(text, strlen(text), h);
		if (strcmp(h, v->last_hash) == 0) {
			free(text);
			continue;
		}

		size_t png_len = 0;
		uint8_t *png = screenshot_text_to_png(text, 0, true, true, &png_len);
		free(text);
		if (!png)
			continue;

		char ts[16], cap[64];
		format_hhmmss(ts, sizeof(ts));
		snprintf(cap, sizeof(cap), "Live \xc2\xb7 %s", ts);

		tg_rate_limit_send(v->chat_id);
		live_build_keyboard(&kb, v->window_id);
		struct tg_error err;
		bool ok = g_cfg.screenshot_as_photo
			? tg_edit_message_media_photo(v->chat_id, v->message_id, png, png_len, cap, &kb, &err)
			: tg_edit_message_media_document(v->chat_id, v->message_id, png, png_len, "screenshot.png", cap, &kb, &err);
		if (ok) {
			pthread_mutex_lock(&g_live_mu);
			for (size_t j = 0; j < g_view_count; j++) {
				if (g_views[j].user_id == v->user_id && g_views[j].thread_id == v->thread_id) {
					snprintf(g_views[j].last_hash, sizeof(g_views[j].last_hash), "%s", h);
					g_views[j].next_edit_after = mono_now() + (double)g_cfg.live_view_interval;
					break;
				}
			}
			pthread_mutex_unlock(&g_live_mu);
		} else if (err.code == 429 && err.retry_after > 0) {
			pthread_mutex_lock(&g_live_mu);
			for (size_t j = 0; j < g_view_count; j++) {
				if (g_views[j].user_id == v->user_id && g_views[j].thread_id == v->thread_id) {
					g_views[j].next_edit_after = mono_now() + (double)err.retry_after;
					break;
				}
			}
			pthread_mutex_unlock(&g_live_mu);
		} else {
			log_warn("live_view_tick_error: %s", err.description);
			live_clear_view(v->user_id, v->thread_id);
		}
		free(png);
	}
}

static bool send_screenshot_media(int64_t chat_id, int64_t thread_id, const uint8_t *png,
				  size_t png_len, const struct tg_keyboard *kb)
{
	struct tg_error err;
	if (g_cfg.screenshot_as_photo)
		return tg_send_photo_bytes(chat_id, thread_id, png, png_len, NULL, kb, NULL, &err);
	return tg_send_document_bytes(chat_id, thread_id, png, png_len, "screenshot.png", NULL, kb, NULL, &err);
}

static bool user_can_access_window(const struct tg_update *u, const char *window_id)
{
	if (router_user_owns_window(u->user_id, window_id))
		return true;
	if (u->has_thread_id && config_is_user_allowed(u->user_id)) {
		char bound_wid[192];
		if (router_get_window_for_chat_thread(u->chat_id, u->thread_id, bound_wid, sizeof(bound_wid)) &&
		    strcmp(bound_wid, window_id) == 0) {
			return true;
		}
	}
	return false;
}

void live_handle_callback(const struct tg_update *u, bool allow_unowned)
{
	const char *data = u->callback_data;

	if (strncmp(data, CB_KEYS_PREFIX, strlen(CB_KEYS_PREFIX)) == 0) {
		const char *rest = data + strlen(CB_KEYS_PREFIX);
		const char *colon = strchr(rest, ':');
		char key_id[32];
		const char *window_id;

		if (!colon || (size_t)(colon - rest) >= sizeof(key_id)) {
			tg_answer_callback(u->callback_id, "Invalid key", true);
			return;
		}
		memcpy(key_id, rest, (size_t)(colon - rest));
		key_id[colon - rest] = '\0';
		window_id = colon + 1;

		const char *tmux_key = NULL;
		const char *label = NULL;
		if (strcmp(key_id, "up") == 0) { tmux_key = "Up"; label = "\xe2\x86\x91"; }
		else if (strcmp(key_id, "dn") == 0) { tmux_key = "Down"; label = "\xe2\x86\x93"; }
		else if (strcmp(key_id, "lt") == 0) { tmux_key = "Left"; label = "\xe2\x86\x90"; }
		else if (strcmp(key_id, "rt") == 0) { tmux_key = "Right"; label = "\xe2\x86\x92"; }
		else if (strcmp(key_id, "esc") == 0) { tmux_key = "Escape"; label = "\xe2\x8e\x8b Esc"; }
		else if (strcmp(key_id, "ent") == 0) { tmux_key = "Enter"; label = "\xe2\x8f\x8e Enter"; }
		else if (strcmp(key_id, "spc") == 0) { tmux_key = "Space"; label = "\xe2\x90\xa3 Space"; }
		else if (strcmp(key_id, "tab") == 0) { tmux_key = "Tab"; label = "\xe2\x87\xa5 Tab"; }
		else if (strcmp(key_id, "cc") == 0) { tmux_key = "C-c"; label = "^C"; }

		if (!tmux_key) {
			tg_answer_callback(u->callback_id, "Unknown key", true);
			return;
		}
		if (!user_can_access_window(u, window_id)) {
			tg_answer_callback(u->callback_id, "Not your session", true);
			return;
		}
		struct tmux_window w;
		if (tmux_find_window_by_id(window_id, &w) &&
		    tmux_send_keys(w.window_id, tmux_key, false, false))
			tg_answer_callback(u->callback_id, label, false);
		else
			tg_answer_callback(u->callback_id, "Failed to send key", true);
		return;
	}

	if (strncmp(data, CB_LIVE_START, strlen(CB_LIVE_START)) == 0) {
		const char *window_id = data + strlen(CB_LIVE_START);
		if (!user_can_access_window(u, window_id)) {
			tg_answer_callback(u->callback_id, "Not your session", true);
			return;
		}
		if (!u->has_thread_id) {
			tg_answer_callback(u->callback_id, "Use in a topic", true);
			return;
		}
		if (live_is_active(u->user_id, u->thread_id)) {
			tg_answer_callback(u->callback_id, "Already live", false);
			return;
		}
		struct tmux_window w;
		if (!tmux_find_window_by_id(window_id, &w)) {
			tg_answer_callback(u->callback_id, "Window not found", true);
			return;
		}
		char *raw = tmux_capture_pane(w.window_id, true, false);
		if (!raw) {
			tg_answer_callback(u->callback_id, "Failed to capture pane", true);
			return;
		}
		char *text = live_tail_lines_ansi(raw, g_cfg.live_view_limit);
		free(raw);
		size_t png_len = 0;
		uint8_t *png = screenshot_text_to_png(text, 0, true, true, &png_len);
		if (!png) {
			free(text);
			tg_answer_callback(u->callback_id, "Render failed", true);
			return;
		}
		char ts[16], cap[64];
		format_hhmmss(ts, sizeof(ts));
		snprintf(cap, sizeof(cap), "Live \xc2\xb7 %s", ts);
		struct tg_keyboard kb;
		live_build_keyboard(&kb, window_id);
		struct tg_error err;
		bool ok = g_cfg.screenshot_as_photo
			? tg_edit_message_media_photo(u->chat_id, u->message_id, png, png_len, cap, &kb, &err)
			: tg_edit_message_media_document(u->chat_id, u->message_id, png, png_len, "screenshot.png", cap, &kb, &err);
		free(png);
		if (!ok) {
			free(text);
			tg_answer_callback(u->callback_id, "Failed to start live view", true);
			return;
		}
		struct live_view_state st = {0};
		st.chat_id = router_resolve_chat_id(u->user_id, u->thread_id);
		st.message_id = u->message_id;
		st.thread_id = u->thread_id;
		st.user_id = u->user_id;
		snprintf(st.window_id, sizeof(st.window_id), "%s", window_id);
		content_hash_hex(text, strlen(text), st.last_hash);
		st.start_time = mono_now();
		free(text);
		start_live_view(&st);
		tg_answer_callback(u->callback_id, "Live started", false);
		return;
	}

	if (strncmp(data, CB_LIVE_STOP, strlen(CB_LIVE_STOP)) == 0) {
		const char *window_id = data + strlen(CB_LIVE_STOP);
		if (!user_can_access_window(u, window_id)) {
			tg_answer_callback(u->callback_id, "Not your session", true);
			return;
		}
		if (!u->has_thread_id) {
			tg_answer_callback(u->callback_id, "Use in a topic", true);
			return;
		}
		live_clear_view(u->user_id, u->thread_id);
		struct tg_keyboard kb;
		live_build_screenshot_keyboard(&kb, window_id);
		tg_edit_message_caption(u->chat_id, u->message_id, "Screenshot", &kb, NULL);
		tg_answer_callback(u->callback_id, "Stopped", false);
		return;
	}

	if (strncmp(data, CB_SCREENSHOT_REFRESH, strlen(CB_SCREENSHOT_REFRESH)) == 0) {
		const char *window_id = data + strlen(CB_SCREENSHOT_REFRESH);
		if (!user_can_access_window(u, window_id)) {
			tg_answer_callback(u->callback_id, "Not your session", true);
			return;
		}
		struct tmux_window w;
		if (!tmux_find_window_by_id(window_id, &w)) {
			tg_answer_callback(u->callback_id, "Window no longer exists", true);
			return;
		}
		char *raw = tmux_capture_pane(w.window_id, true, false);
		if (!raw) {
			tg_answer_callback(u->callback_id, "Failed to capture pane", true);
			return;
		}
		size_t png_len = 0;
		uint8_t *png = screenshot_text_to_png(raw, 0, true, false, &png_len);
		free(raw);
		if (!png) {
			tg_answer_callback(u->callback_id, "Render failed", true);
			return;
		}
		struct tg_keyboard kb;
		live_build_screenshot_keyboard(&kb, window_id);
		struct tg_error err;
		bool ok = g_cfg.screenshot_as_photo
			? tg_edit_message_media_photo(u->chat_id, u->message_id, png, png_len, NULL, &kb, &err)
			: tg_edit_message_media_document(u->chat_id, u->message_id, png, png_len, "screenshot.png", NULL, &kb, &err);
		free(png);
		tg_answer_callback(u->callback_id, ok ? "Refreshed" : "Failed to refresh", !ok);
		return;
	}

	if (strncmp(data, CB_STATUS_SCREENSHOT, strlen(CB_STATUS_SCREENSHOT)) == 0) {
		const char *window_id = data + strlen(CB_STATUS_SCREENSHOT);
		if (!allow_unowned && !router_user_owns_window(u->user_id, window_id)) {
			tg_answer_callback(u->callback_id, "Not your session", true);
			return;
		}
		if (!u->has_thread_id) {
			tg_answer_callback(u->callback_id, "Use in a topic", true);
			return;
		}
		struct tmux_window w;
		if (!tmux_find_window_by_id(window_id, &w)) {
			tg_answer_callback(u->callback_id, "Window not found", true);
			return;
		}
		char *raw = tmux_capture_pane(w.window_id, true, false);
		if (!raw) {
			tg_answer_callback(u->callback_id, "Failed to capture", true);
			return;
		}
		size_t png_len = 0;
		uint8_t *png = screenshot_text_to_png(raw, 0, true, false, &png_len);
		free(raw);
		if (!png) {
			tg_answer_callback(u->callback_id, "Render failed", true);
			return;
		}
		struct tg_keyboard kb;
		live_build_screenshot_keyboard(&kb, window_id);
		int64_t chat_id = router_resolve_chat_id(u->user_id, u->thread_id);
		if (allow_unowned && u->chat_id != 0)
			chat_id = u->chat_id;
		bool ok = send_screenshot_media(chat_id, u->thread_id, png, png_len, &kb);
		free(png);
		tg_answer_callback(u->callback_id, ok ? "Sent" : "Failed to send screenshot", !ok);
	}
}

static bool resolve_bound_window_for_cmd(const struct tg_update *u, char *out_wid, size_t cap)
{
	if (router_get_window_for_thread(u->user_id, u->thread_id, out_wid, cap))
		return true;

	struct tmux_window sessions[128];
	size_t n = tmux_list_sessions(sessions, 128);
	for (size_t i = 0; i < n; i++) {
		if (sessions[i].has_topic_ref &&
		    sessions[i].topic_chat_id == u->chat_id &&
		    sessions[i].topic_thread_id == u->thread_id) {
			char tname[128];
			tmux_topic_name_from_session(sessions[i].window_name, tname, sizeof(tname));
			router_bind_thread(u->user_id, u->thread_id, sessions[i].window_id, tname);
			router_set_group_chat_id(u->user_id, u->thread_id, u->chat_id);
			snprintf(out_wid, cap, "%s", sessions[i].window_id);
			return true;
		}
	}
	return false;
}

void cmd_screenshot(const struct tg_update *u)
{
	if (!u->has_thread_id) {
		tg_safe_reply(u->chat_id, 0, u->message_id, "Use this command inside a topic.", NULL);
		return;
	}
	char wid[192];
	if (!resolve_bound_window_for_cmd(u, wid, sizeof(wid))) {
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, "This topic is not bound to any session.", NULL);
		return;
	}
	struct tmux_window w;
	if (!tmux_find_window_by_id(wid, &w)) {
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, "Window no longer exists.", NULL);
		return;
	}
	char *raw = tmux_capture_pane(w.window_id, true, false);
	if (!raw) {
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, "Failed to capture terminal.", NULL);
		return;
	}
	size_t png_len = 0;
	uint8_t *png = screenshot_text_to_png(raw, 0, true, false, &png_len);
	free(raw);
	if (!png) {
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, "Failed to render screenshot.", NULL);
		return;
	}
	struct tg_keyboard kb;
	live_build_screenshot_keyboard(&kb, wid);
	int64_t chat_id = router_resolve_chat_id(u->user_id, u->thread_id);
	if (!send_screenshot_media(chat_id, u->thread_id, png, png_len, &kb))
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, "Failed to send screenshot.", NULL);
	free(png);
}

void cmd_live(const struct tg_update *u)
{
	if (!u->has_thread_id) {
		tg_safe_reply(u->chat_id, 0, u->message_id, "Use this command inside a topic.", NULL);
		return;
	}
	if (live_is_active(u->user_id, u->thread_id)) {
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, "Live view already running.", NULL);
		return;
	}
	char wid[192];
	if (!resolve_bound_window_for_cmd(u, wid, sizeof(wid))) {
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, "This topic is not bound to any session.", NULL);
		return;
	}
	struct tmux_window w;
	if (!tmux_find_window_by_id(wid, &w)) {
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, "Window no longer exists.", NULL);
		return;
	}
	char *raw = tmux_capture_pane(w.window_id, true, false);
	if (!raw) {
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, "Failed to capture terminal.", NULL);
		return;
	}
	char *text = live_tail_lines_ansi(raw, g_cfg.live_view_limit);
	free(raw);

	size_t png_len = 0;
	uint8_t *png = screenshot_text_to_png(text, 0, true, true, &png_len);
	if (!png) {
		free(text);
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, "Failed to render live view.", NULL);
		return;
	}
	char ts[16], cap[64];
	format_hhmmss(ts, sizeof(ts));
	snprintf(cap, sizeof(cap), "Live \xc2\xb7 %s", ts);

	struct tg_keyboard kb;
	live_build_keyboard(&kb, wid);
	int64_t chat_id = router_resolve_chat_id(u->user_id, u->thread_id);
	int64_t msg_id = 0;
	struct tg_error err;
	bool ok = g_cfg.screenshot_as_photo
		? tg_send_photo_bytes(chat_id, u->thread_id, png, png_len, cap, &kb, &msg_id, &err)
		: tg_send_document_bytes(chat_id, u->thread_id, png, png_len, "screenshot.png", cap, &kb, &msg_id, &err);
	free(png);

	if (!ok || msg_id <= 0) {
		free(text);
		tg_safe_reply(u->chat_id, u->thread_id, u->message_id, "Failed to start live view.", NULL);
		return;
	}

	struct live_view_state st = {0};
	st.chat_id = chat_id;
	st.message_id = msg_id;
	st.thread_id = u->thread_id;
	st.user_id = u->user_id;
	snprintf(st.window_id, sizeof(st.window_id), "%s", wid);
	content_hash_hex(text, strlen(text), st.last_hash);
	st.start_time = mono_now();
	free(text);
	start_live_view(&st);
}
