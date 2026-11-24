#ifndef CCGRAM_H
#define CCGRAM_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <pthread.h>

#define CCGRAM_VERSION "0.1.0-c"
#define CCGRAM_TOPIC_OPTION "@ccgram_topic"
#define CCGRAM_COMMAND_PREFIX "//"
#define CCGRAM_UPLOAD_DIR ".ccgram-uploads"
#define CCGRAM_MAX_CB_DATA 64
#define CCGRAM_TG_MAX_MSG 4096
#define CCGRAM_TG_SPLIT_LEN 3000
#define CCGRAM_SEND_MAX_FILE_SIZE (50LL * 1024LL * 1024LL)
#define CCGRAM_TOPIC_CHECK_INTERVAL 60.0

/* Callback prefixes (must match Python callback_data.py) */
#define CB_SCREENSHOT_REFRESH "ss:ref:"
#define CB_SCREEN_DIFF "sd:"
#define CB_SESSIONS_REFRESH "sess:ref"
#define CB_SESSIONS_KILL "sess:kill:"
#define CB_SESSIONS_KILL_CONFIRM "sess:killok:"
#define CB_SESSIONS_RENAME "sess:rn:"
#define CB_SESSIONS_SCREENSHOT "sess:scr:"
#define CB_STATUS_SCREENSHOT "st:ss:"
#define CB_KEYS_PREFIX "kb:"
#define CB_LIVE_START "lv:go:"
#define CB_LIVE_STOP "lv:stop:"

/* ── Dynamic String Buffer ─────────────────────────────────────────── */

struct strbuf {
	char *data;
	size_t len;
	size_t cap;
};

void sb_init(struct strbuf *sb);
void sb_free(struct strbuf *sb);
bool sb_reserve(struct strbuf *sb, size_t extra);
bool sb_append(struct strbuf *sb, const char *s, size_t n);
bool sb_append_str(struct strbuf *sb, const char *s);
bool sb_appendf(struct strbuf *sb, const char *fmt, ...);
char *sb_detach(struct strbuf *sb);

/* ── Logging ───────────────────────────────────────────────────────── */

enum log_level {
	LOG_DEBUG = 0,
	LOG_INFO = 1,
	LOG_WARN = 2,
	LOG_ERROR = 3,
};

void log_set_level(enum log_level lvl);
void log_msg(enum log_level lvl, const char *fmt, ...);
void log_throttled(const char *key, double interval_s, const char *fmt, ...);
void log_throttle_reset(const char *key);
void log_throttle_sweep(void);

#define log_debug(...) log_msg(LOG_DEBUG, __VA_ARGS__)
#define log_info(...) log_msg(LOG_INFO, __VA_ARGS__)
#define log_warn(...) log_msg(LOG_WARN, __VA_ARGS__)
#define log_error(...) log_msg(LOG_ERROR, __VA_ARGS__)

/* ── Utilities ─────────────────────────────────────────────────────── */

double mono_now(void);
uint64_t time_ns_now(void);
char *xstrdup(const char *s);
void content_hash_hex(const char *data, size_t len, char out[33]);
size_t utf16_len(const char *utf8, size_t byte_len);
int split_message(const char *text, size_t max_utf16, char ***out_parts, size_t *out_count);
void free_str_array(char **arr, size_t count);
bool path_is_dir(const char *path);
bool path_is_file(const char *path);
bool path_is_symlink(const char *path);
bool mkdir_p(const char *path, int mode);

/* ── Config ────────────────────────────────────────────────────────── */

#define MAX_ALLOWED_USERS 128

struct ccgram_config {
	char config_dir[512];
	char telegram_bot_token[256];
	int64_t allowed_users[MAX_ALLOWED_USERS];
	size_t allowed_user_count;

	char tmux_session_prefix[64];
	char tmux_session_name[128];
	char session_working_directory[512];
	double status_poll_interval;

	bool has_group_id;
	int64_t group_id;

	int live_view_interval;
	int live_view_timeout;
	int live_view_limit;

	int file_size_limit_mb;

	bool topic_status_diff_enabled;
	int topic_status_diff_interval;
	int screen_diff_min_limit;
	int screen_diff_max_limit;
	double screen_diff_step_percent;
	int screen_diff_limit;
	bool screen_diff_arrows;

	int screenshot_font_size;
	int screenshot_palette_colors;
	bool screenshot_as_photo;

	enum log_level log_level;
};

extern struct ccgram_config g_cfg;

int config_init(void);
bool config_is_user_allowed(int64_t user_id);
void config_resolve_dir(char *out, size_t cap);

/* ── Thread Router, Topic Tail & User State ────────────────────────── */

struct thread_binding {
	int64_t user_id;
	int64_t thread_id;
	char window_id[192];
};

void router_init(void);
void router_reset(void);
void router_bind_thread(int64_t user_id, int64_t thread_id, const char *window_id, const char *window_name);
bool router_unbind_thread(int64_t user_id, int64_t thread_id, char *prev_wid, size_t prev_cap);
bool router_get_window_for_thread(int64_t user_id, int64_t thread_id, char *out, size_t cap);
size_t router_get_all_thread_windows(int64_t user_id, int64_t *out_tids, char (*out_wids)[192], size_t max_items);
size_t router_iter_bindings(struct thread_binding *out, size_t max_items);
void router_set_group_chat_id(int64_t user_id, int64_t thread_id, int64_t chat_id);
int64_t router_resolve_chat_id(int64_t user_id, int64_t thread_id);
bool router_get_window_for_chat_thread(int64_t chat_id, int64_t thread_id, char *out, size_t cap);
void router_get_display_name(const char *window_id, char *out, size_t cap);
bool router_user_owns_window(int64_t user_id, const char *window_id);

void topic_tail_record(int64_t chat_id, int64_t thread_id, int64_t message_id);
bool topic_tail_is_last(int64_t chat_id, int64_t thread_id, int64_t message_id);
void topic_tail_clear(int64_t chat_id, int64_t thread_id);
void topic_tail_reset(void);

void user_state_set_rename(int64_t user_id, int64_t thread_id, const char *window_id);
bool user_state_pop_rename(int64_t user_id, int64_t thread_id, char *out_wid, size_t cap, int64_t *out_tid);

/* ── Tmux Manager ──────────────────────────────────────────────────── */

struct tmux_window {
	char window_id[192];
	char window_name[128];
	char cwd[512];
	bool has_topic_ref;
	int64_t topic_chat_id;
	int64_t topic_thread_id;
};

struct pane_info {
	char pane_id[32];
	bool active;
	char path[512];
	bool dead;
};

void tmux_init(void);
void tmux_topic_session_name(const char *topic_name, char *out, size_t cap);
void tmux_topic_name_from_session(const char *session_name, char *out, size_t cap);
size_t tmux_list_sessions(struct tmux_window *out, size_t max_items);
bool tmux_set_session_topic(const char *session_name, int64_t chat_id, int64_t thread_id);
bool tmux_clear_session_topic(const char *session_name);
bool tmux_find_window_by_id(const char *window_id, struct tmux_window *out);
char *tmux_capture_pane(const char *window_id, bool with_ansi, bool include_history);
char *tmux_capture_pane_by_id(const char *pane_id, bool with_ansi, bool include_history, const char *window_id);
bool tmux_send_keys(const char *window_id, const char *text, bool enter, bool literal);
bool tmux_send_keys_to_pane(const char *pane_id, const char *text, bool enter, bool literal, const char *window_id);
bool tmux_send_to_window(const char *window_id, const char *text, char *err_buf, size_t err_cap);
bool tmux_kill_session(const char *session_name);
bool tmux_rename_session(const char *session_name, const char *new_name);
bool tmux_session_exists(const char *session_name, char *err_buf, size_t err_cap);
bool tmux_switch_client(const char *session_name);
bool tmux_create_session(const char *session_name, const char *work_dir, const char *window_name, char *out_wid, size_t wid_cap, char *err_buf, size_t err_cap);

size_t tmux_list_panes(const char *window_id, struct pane_info *out, size_t max_items);
bool tmux_kill_pane(const char *pane_id, const char *window_id);
bool tmux_get_sidecar_pane_id(const char *window_id, char *out, size_t cap);
bool tmux_get_sidecar_marker(const char *window_id, char *out, size_t cap);
void tmux_forget_sidecar_pane(const char *window_id);
bool tmux_start_sidecar_command(const char *window_id, const char *command, char *out_pane_id, size_t pid_cap, char *out_marker, size_t m_cap);
bool extract_sidecar_output(const char *text, const char *marker, char **out_text, bool *out_completed);

/* ── Markdown -> Telegram Entities ─────────────────────────────────── */

struct tg_entity {
	char type[32];
	int offset;
	int length;
	char url[512];
	char language[64];
};

void convert_to_entities(const char *markdown, char **out_plain, struct tg_entity **out_ents, size_t *out_count);

/* ── Screenshot Renderer (ANSI -> PNG) ─────────────────────────────── */

uint8_t *screenshot_text_to_png(const char *text, int font_size, bool with_ansi, bool live_mode, size_t *out_len);

/* ── Telegram Bot API Client ───────────────────────────────────────── */

struct tg_button {
	char text[96];
	char callback_data[CCGRAM_MAX_CB_DATA + 1];
};

struct tg_keyboard {
	struct tg_button rows[16][6];
	size_t row_lens[16];
	size_t num_rows;
};

struct tg_error {
	int code;
	int retry_after;
	char description[256];
};

struct tg_update {
	int64_t update_id;
	bool is_callback;

	/* message fields */
	int64_t message_id;
	int64_t chat_id;
	char chat_type[32];
	int64_t user_id;
	bool has_thread_id;
	int64_t thread_id;
	char *text;
	char *caption;

	bool has_photo;
	char photo_file_id[256];
	char photo_unique_id[128];
	int64_t photo_file_size;

	bool has_document;
	char doc_file_id[256];
	char doc_file_name[256];
	int64_t doc_file_size;

	bool is_topic_created;
	char topic_created_name[256];
	bool is_topic_closed;
	bool is_topic_edited;
	char topic_edited_name[256];

	/* callback_query fields */
	char callback_id[128];
	char callback_data[128];
};

void tg_api_init(void);
void tg_api_cleanup(void);
void tg_update_free(struct tg_update *u);
bool tg_is_thread_gone(const struct tg_error *err);
void tg_rate_limit_send(int64_t chat_id);

int tg_get_updates(int64_t offset, int timeout_s, struct tg_update **out_updates, size_t *out_count, struct tg_error *err);
bool tg_send_message_plain(int64_t chat_id, int64_t thread_id, int64_t reply_to_msg_id,
			   const char *text, const struct tg_entity *ents, size_t ent_count,
			   const struct tg_keyboard *kb, bool force_reply,
			   int64_t *out_msg_id, struct tg_error *err);
bool tg_edit_message_text_plain(int64_t chat_id, int64_t message_id, const char *text,
				const struct tg_entity *ents, size_t ent_count,
				const struct tg_keyboard *kb, struct tg_error *err);
bool tg_edit_message_reply_markup(int64_t chat_id, int64_t message_id, const struct tg_keyboard *kb, struct tg_error *err);
bool tg_send_photo_bytes(int64_t chat_id, int64_t thread_id, const uint8_t *png, size_t len,
			 const char *caption, const struct tg_keyboard *kb, int64_t *out_msg_id, struct tg_error *err);
bool tg_send_document_bytes(int64_t chat_id, int64_t thread_id, const uint8_t *data, size_t len,
			    const char *filename, const char *caption, const struct tg_keyboard *kb,
			    int64_t *out_msg_id, struct tg_error *err);
bool tg_edit_message_media_photo(int64_t chat_id, int64_t message_id, const uint8_t *png, size_t len,
				 const char *caption, const struct tg_keyboard *kb, struct tg_error *err);
bool tg_edit_message_media_document(int64_t chat_id, int64_t message_id, const uint8_t *data, size_t len,
				    const char *filename, const char *caption, const struct tg_keyboard *kb, struct tg_error *err);
bool tg_edit_message_caption(int64_t chat_id, int64_t message_id, const char *caption,
			     const struct tg_keyboard *kb, struct tg_error *err);
bool tg_answer_callback(const char *callback_id, const char *text, bool show_alert);
bool tg_send_chat_action(int64_t chat_id, int64_t thread_id, const char *action);
bool tg_edit_forum_topic(int64_t chat_id, int64_t thread_id, const char *name, struct tg_error *err);
bool tg_close_forum_topic(int64_t chat_id, int64_t thread_id, struct tg_error *err);
bool tg_reopen_forum_topic(int64_t chat_id, int64_t thread_id, struct tg_error *err);
bool tg_delete_forum_topic(int64_t chat_id, int64_t thread_id, struct tg_error *err);
bool tg_download_file(const char *file_id, const char *dest_path, struct tg_error *err);

/* High-level helpers with entity conversion + fallback + splitting + rate limit */
int64_t tg_safe_reply(int64_t chat_id, int64_t thread_id, int64_t reply_to_msg_id,
		      const char *markdown_text, const struct tg_keyboard *kb);
int64_t tg_rate_limit_send_message(int64_t chat_id, int64_t thread_id, const char *markdown_text,
				   const struct tg_keyboard *kb, int split_max_len);
bool tg_safe_edit(int64_t chat_id, int64_t message_id, const char *markdown_text, const struct tg_keyboard *kb);
bool tg_edit_with_fallback(int64_t chat_id, int64_t message_id, const char *markdown_text,
			   const struct tg_keyboard *kb, struct tg_error *err);

/* ── Screen Diff ───────────────────────────────────────────────────── */

void diff_init(void);
void diff_start_sidecar(int64_t chat_id, int64_t thread_id, const char *window_id, const char *title);
void diff_mark_activity(int64_t chat_id, int64_t thread_id, const char *window_id, int64_t message_id);
void diff_update(int64_t chat_id, int64_t thread_id, const char *window_id, const char *pane_text,
		 const char *target, bool force);
void diff_prime(int64_t chat_id, int64_t thread_id, const char *window_id, const char *pane_text, const char *target);
void diff_clear_topic(int64_t thread_id);
void diff_reset(void);
int diff_toggle_arrows(int64_t chat_id, int64_t thread_id);
void diff_handle_callback(const struct tg_update *u);
char *diff_tail_lines(const char *text, int limit);
void diff_build_keyboard(struct tg_keyboard *kb, bool arrows);

/* ── Live View & Screenshot ────────────────────────────────────────── */

void live_init(void);
void live_clear_view(int64_t user_id, int64_t thread_id);
bool live_is_active(int64_t user_id, int64_t thread_id);
void live_tick_all(void);
char *live_tail_lines_ansi(const char *text, int limit);
void live_build_keyboard(struct tg_keyboard *kb, const char *window_id);
void live_build_screenshot_keyboard(struct tg_keyboard *kb, const char *window_id);
void live_handle_callback(const struct tg_update *u, bool allow_unowned);
void cmd_screenshot(const struct tg_update *u);
void cmd_live(const struct tg_update *u);

/* ── Handlers & Polling ────────────────────────────────────────────── */

void handlers_dispatch_update(const struct tg_update *u);
void handlers_reconcile(bool verify);
void *polling_thread_main(void *arg);
void clear_topic_state(int64_t user_id, int64_t thread_id, const char *window_id);

enum cmd_match_status {
	CMD_MATCH_NONE = 0,
	CMD_MATCH_OK = 1,
	CMD_MATCH_AMBIGUOUS = 2,
};

struct cmd_def {
	const char *name;
	const char *aliases[4];
	const char *params;
	const char *description;
	void (*fn)(const struct tg_update *u, const char *args);
};

const struct cmd_def *handlers_get_commands(size_t *out_count);
enum cmd_match_status match_command_token(const char *token, const struct cmd_def *cmds, size_t count,
					  const struct cmd_def **out_cmd,
					  const char **out_candidates, size_t max_cand, size_t *out_cand_count);

void sanitize_filename(const char *in, char *out, size_t cap);
void sanitize_caption(const char *in, char *out, size_t cap);

extern volatile int g_running;

#endif /* CCGRAM_H */
