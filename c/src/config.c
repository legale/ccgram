#include "ccgram.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

struct ccgram_config g_cfg;

static void trim_inplace(char *s)
{
	char *start = s;
	size_t len;

	while (*start && isspace((unsigned char)*start))
		start++;
	if (start != s)
		memmove(s, start, strlen(start) + 1);
	len = strlen(s);
	while (len > 0 && isspace((unsigned char)s[len - 1]))
		s[--len] = '\0';
}

static void load_dotenv_file(const char *path)
{
	FILE *fp;
	char line[1024];

	fp = fopen(path, "r");
	if (!fp)
		return;

	while (fgets(line, sizeof(line), fp)) {
		char *eq;
		char *key;
		char *val;
		size_t vlen;

		trim_inplace(line);
		if (line[0] == '\0' || line[0] == '#')
			continue;
		if (strncmp(line, "export ", 7) == 0) {
			memmove(line, line + 7, strlen(line + 7) + 1);
			trim_inplace(line);
		}
		eq = strchr(line, '=');
		if (!eq)
			continue;
		*eq = '\0';
		key = line;
		val = eq + 1;
		trim_inplace(key);
		trim_inplace(val);
		vlen = strlen(val);
		if (vlen >= 2 && ((val[0] == '"' && val[vlen - 1] == '"') ||
				  (val[0] == '\'' && val[vlen - 1] == '\''))) {
			val[vlen - 1] = '\0';
			val++;
		}
		if (*key)
			setenv(key, val, 0); /* override=0: existing env or earlier .env wins */
	}
	fclose(fp);
	log_debug("Loaded env from %s", path);
}

void config_resolve_dir(char *out, size_t cap)
{
	const char *env_dir = getenv("CCGRAM_DIR");
	const char *home;

	if (env_dir && *env_dir) {
		snprintf(out, cap, "%s", env_dir);
		return;
	}
	home = getenv("HOME");
	if (!home || !*home)
		home = ".";
	snprintf(out, cap, "%s/.ccgram", home);
}

static int parse_int_env(const char *name, int def_val)
{
	const char *raw = getenv(name);
	char *end = NULL;
	long v;

	if (!raw || !*raw)
		return def_val;
	v = strtol(raw, &end, 10);
	if (end == raw || *end != '\0')
		return def_val;
	return (int)v;
}

static double parse_double_env(const char *name, double def_val)
{
	const char *raw = getenv(name);
	char *end = NULL;
	double v;

	if (!raw || !*raw)
		return def_val;
	v = strtod(raw, &end);
	if (end == raw || *end != '\0')
		return def_val;
	return v;
}

static bool parse_bool_env(const char *name, bool def_val)
{
	const char *raw = getenv(name);

	if (!raw || !*raw)
		return def_val;
	if (strcmp(raw, "1") == 0 || strcasecmp(raw, "true") == 0 || strcasecmp(raw, "yes") == 0)
		return true;
	if (strcmp(raw, "0") == 0 || strcasecmp(raw, "false") == 0 || strcasecmp(raw, "no") == 0)
		return false;
	return def_val;
}

static void parse_log_level(void)
{
	const char *lvl = getenv("CCGRAM_LOG_LEVEL");

	g_cfg.log_level = LOG_INFO;
	if (!lvl)
		return;
	if (strcasecmp(lvl, "DEBUG") == 0)
		g_cfg.log_level = LOG_DEBUG;
	else if (strcasecmp(lvl, "WARNING") == 0 || strcasecmp(lvl, "WARN") == 0)
		g_cfg.log_level = LOG_WARN;
	else if (strcasecmp(lvl, "ERROR") == 0)
		g_cfg.log_level = LOG_ERROR;
	log_set_level(g_cfg.log_level);
}

int config_init(void)
{
	char path_buf[600];
	const char *tok;
	const char *allowed;
	const char *grp;
	const char *s;

	memset(&g_cfg, 0, sizeof(g_cfg));
	config_resolve_dir(g_cfg.config_dir, sizeof(g_cfg.config_dir));
	mkdir_p(g_cfg.config_dir, 0755);

	/* Priority: local .env > $CCGRAM_DIR/.env > .env-default > $CCGRAM_DIR/.env-default > ../.env-default */
	load_dotenv_file(".env");
	snprintf(path_buf, sizeof(path_buf), "%s/.env", g_cfg.config_dir);
	load_dotenv_file(path_buf);
	load_dotenv_file(".env-default");
	snprintf(path_buf, sizeof(path_buf), "%s/.env-default", g_cfg.config_dir);
	load_dotenv_file(path_buf);
	load_dotenv_file("../.env-default");

	parse_log_level();

	tok = getenv("TELEGRAM_BOT_TOKEN");
	if (!tok || !*tok) {
		log_error("TELEGRAM_BOT_TOKEN environment variable is required");
		return -1;
	}
	snprintf(g_cfg.telegram_bot_token, sizeof(g_cfg.telegram_bot_token), "%s", tok);

	allowed = getenv("ALLOWED_USERS");
	if (!allowed || !*allowed) {
		log_error("ALLOWED_USERS environment variable is required");
		return -1;
	}
	{
		char *copy = xstrdup(allowed);
		char *save = NULL;
		char *item;

		for (item = strtok_r(copy, ",", &save); item; item = strtok_r(NULL, ",", &save)) {
			char *end = NULL;
			long long uid;
			trim_inplace(item);
			if (!*item)
				continue;
			uid = strtoll(item, &end, 10);
			if (end == item || *end != '\0') {
				log_error("ALLOWED_USERS contains non-numeric value: %s", item);
				free(copy);
				return -1;
			}
			if (g_cfg.allowed_user_count < MAX_ALLOWED_USERS)
				g_cfg.allowed_users[g_cfg.allowed_user_count++] = (int64_t)uid;
		}
		free(copy);
	}
	if (g_cfg.allowed_user_count == 0) {
		log_error("ALLOWED_USERS must contain at least one user ID");
		return -1;
	}

	s = getenv("TMUX_SESSION_PREFIX");
	snprintf(g_cfg.tmux_session_prefix, sizeof(g_cfg.tmux_session_prefix), "%s",
		 (s && *s) ? s : "cc_");

	s = getenv("TMUX_SESSION_NAME");
	snprintf(g_cfg.tmux_session_name, sizeof(g_cfg.tmux_session_name), "%s",
		 (s && *s) ? s : "ccgram");

	s = getenv("CCGRAM_SESSION_WORKING_DIRECTORY");
	snprintf(g_cfg.session_working_directory, sizeof(g_cfg.session_working_directory), "%s",
		 (s && *s) ? s : "~");

	g_cfg.status_poll_interval = parse_double_env("CCGRAM_STATUS_POLL_INTERVAL", 1.0);
	if (g_cfg.status_poll_interval < 0.5)
		g_cfg.status_poll_interval = 0.5;

	grp = getenv("CCGRAM_GROUP_ID");
	if (grp && *grp) {
		char *end = NULL;
		long long gid = strtoll(grp, &end, 10);
		if (end == grp || *end != '\0') {
			log_error("CCGRAM_GROUP_ID must be a valid integer: %s", grp);
			return -1;
		}
		g_cfg.has_group_id = true;
		g_cfg.group_id = (int64_t)gid;
	}

	g_cfg.live_view_interval = parse_int_env("CCGRAM_LIVE_VIEW_INTERVAL", 5);
	if (g_cfg.live_view_interval < 1)
		g_cfg.live_view_interval = 1;
	g_cfg.live_view_timeout = parse_int_env("CCGRAM_LIVE_VIEW_TIMEOUT", 300);
	if (g_cfg.live_view_timeout < 1)
		g_cfg.live_view_timeout = 1;
	g_cfg.live_view_limit = parse_int_env("CCGRAM_LIVE_VIEW_LIMIT", 2500);
	if (g_cfg.live_view_limit < 100)
		g_cfg.live_view_limit = 100;

	g_cfg.file_size_limit_mb = parse_int_env("CCGRAM_FILE_SIZE_LIMIT_MB", 1024);
	if (g_cfg.file_size_limit_mb < 1)
		g_cfg.file_size_limit_mb = 1;

	g_cfg.topic_status_diff_enabled = parse_bool_env("CCGRAM_TOPIC_STATUS_DIFF_ENABLED", true);
	g_cfg.topic_status_diff_interval = parse_int_env("CCGRAM_TOPIC_STATUS_DIFF_INTERVAL", 3);
	if (g_cfg.topic_status_diff_interval < 1)
		g_cfg.topic_status_diff_interval = 1;

	g_cfg.screen_diff_min_limit = parse_int_env("CCGRAM_SCREEN_DIFF_MIN_LIMIT", 100);
	if (g_cfg.screen_diff_min_limit <= 0)
		g_cfg.screen_diff_min_limit = 100;
	g_cfg.screen_diff_max_limit = parse_int_env("CCGRAM_SCREEN_DIFF_MAX_LIMIT", 4000);
	if (g_cfg.screen_diff_max_limit < g_cfg.screen_diff_min_limit)
		g_cfg.screen_diff_max_limit = g_cfg.screen_diff_min_limit;
	g_cfg.screen_diff_step_percent = parse_double_env("CCGRAM_SCREEN_DIFF_STEP_PERCENT", 0.25);
	if (g_cfg.screen_diff_step_percent <= 0.0)
		g_cfg.screen_diff_step_percent = 0.25;
	g_cfg.screen_diff_limit = parse_int_env("CCGRAM_SCREEN_DIFF_LIMIT", 3000);
	if (g_cfg.screen_diff_limit < g_cfg.screen_diff_min_limit)
		g_cfg.screen_diff_limit = g_cfg.screen_diff_min_limit;
	g_cfg.screen_diff_arrows = parse_bool_env("CCGRAM_SCREEN_DIFF_ARROWS", false);

	g_cfg.screenshot_font_size = parse_int_env("CCGRAM_SCREENSHOT_FONT_SIZE", 15);
	if (g_cfg.screenshot_font_size < 8)
		g_cfg.screenshot_font_size = 8;
	g_cfg.screenshot_palette_colors = parse_int_env("CCGRAM_SCREENSHOT_PALETTE_COLORS", 64);
	if (g_cfg.screenshot_palette_colors < 0)
		g_cfg.screenshot_palette_colors = 0;
	g_cfg.screenshot_as_photo = parse_bool_env("CCGRAM_SCREENSHOT_AS_PHOTO", true);

	return 0;
}

bool config_is_user_allowed(int64_t user_id)
{
	for (size_t i = 0; i < g_cfg.allowed_user_count; i++) {
		if (g_cfg.allowed_users[i] == user_id)
			return true;
	}
	return false;
}
