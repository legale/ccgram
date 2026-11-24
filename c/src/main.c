#include "ccgram.h"

#include <getopt.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

volatile int g_running = 1;

static void on_signal(int sig)
{
	(void)sig;
	g_running = 0;
}

static void print_help(void)
{
	printf("Usage: ccgram [run|status] [OPTIONS]\n\n"
	       "Manage tmux sessions from Telegram.\n\n"
	       "Options:\n"
	       "  -v, --verbose            Enable debug logging.\n"
	       "      --log-level LEVEL    Logging level (DEBUG, INFO, WARNING, ERROR).\n"
	       "      --config-dir PATH    Config directory (default: ~/.ccgram).\n"
	       "      --allowed-users IDS  Comma-separated Telegram user IDs.\n"
	       "      --tmux-session NAME  Tmux session name (default: ccgram).\n"
	       "      --group-id ID        Restrict to one Telegram group.\n"
	       "      --version            Show version and exit.\n"
	       "  -h, --help               Show this help and exit.\n");
}

static int status_main(void)
{
	const char *prefix = getenv("TMUX_SESSION_PREFIX");
	struct tmux_window sessions[128];
	size_t ns;
	size_t managed = 0;

	if (!prefix || !*prefix)
		prefix = "cc_";
	snprintf(g_cfg.tmux_session_prefix, sizeof(g_cfg.tmux_session_prefix), "%s", prefix);

	ns = tmux_list_sessions(sessions, 128);
	for (size_t i = 0; i < ns; i++) {
		if (strncmp(sessions[i].window_name, prefix, strlen(prefix)) == 0)
			managed++;
	}

	printf("ccgram %s\n", CCGRAM_VERSION);
	printf("Managed tmux sessions: %zu\n", managed);
	if (managed > 0)
		putchar('\n');
	for (size_t i = 0; i < ns; i++) {
		if (strncmp(sessions[i].window_name, prefix, strlen(prefix)) != 0)
			continue;
		if (sessions[i].has_topic_ref) {
			printf("  %s -> %lld:%lld%s%s\n", sessions[i].window_name,
			       (long long)sessions[i].topic_chat_id,
			       (long long)sessions[i].topic_thread_id,
			       sessions[i].cwd[0] ? " " : "",
			       sessions[i].cwd);
		} else {
			printf("  %s -> unbound%s%s\n", sessions[i].window_name,
			       sessions[i].cwd[0] ? " " : "",
			       sessions[i].cwd);
		}
	}
	return 0;
}

int main(int argc, char **argv)
{
	int arg_start = 1;
	bool is_status = false;

	if (argc >= 2) {
		if (strcmp(argv[1], "status") == 0) {
			is_status = true;
			arg_start = 2;
		} else if (strcmp(argv[1], "run") == 0) {
			arg_start = 2;
		}
	}

	static const struct option long_opts[] = {
		{ "verbose",       no_argument,       NULL, 'v' },
		{ "log-level",     required_argument, NULL, 'l' },
		{ "config-dir",    required_argument, NULL, 'c' },
		{ "allowed-users", required_argument, NULL, 'a' },
		{ "tmux-session",  required_argument, NULL, 't' },
		{ "group-id",      required_argument, NULL, 'g' },
		{ "provider",      required_argument, NULL, 'p' },
		{ "version",       no_argument,       NULL, 'V' },
		{ "help",          no_argument,       NULL, 'h' },
		{ NULL,            0,                 NULL,  0  }
	};

	optind = arg_start;
	int opt;
	while ((opt = getopt_long(argc, argv, "vh", long_opts, NULL)) != -1) {
		switch (opt) {
		case 'v':
			setenv("CCGRAM_LOG_LEVEL", "DEBUG", 1);
			break;
		case 'l':
			setenv("CCGRAM_LOG_LEVEL", optarg, 1);
			break;
		case 'c':
			setenv("CCGRAM_DIR", optarg, 1);
			break;
		case 'a':
			setenv("ALLOWED_USERS", optarg, 1);
			break;
		case 't':
			setenv("TMUX_SESSION_NAME", optarg, 1);
			break;
		case 'g':
			setenv("CCGRAM_GROUP_ID", optarg, 1);
			break;
		case 'p':
			break; /* deprecated no-op */
		case 'V':
			printf("ccgram %s\n", CCGRAM_VERSION);
			return 0;
		case 'h':
			print_help();
			return 0;
		default:
			return 1;
		}
	}

	if (is_status)
		return status_main();

	if (config_init() != 0)
		return 1;

	router_init();
	tmux_init();
	diff_init();
	live_init();
	tg_api_init();
	poll_init();

	struct sigaction sa;
	memset(&sa, 0, sizeof(sa));
	sa.sa_handler = on_signal;
	sigaction(SIGINT, &sa, NULL);
	sigaction(SIGTERM, &sa, NULL);
	signal(SIGPIPE, SIG_IGN);

	pthread_t poll_thr;
	pthread_attr_t poll_attr;
	pthread_attr_init(&poll_attr);
	pthread_attr_setstacksize(&poll_attr, 2 * 1024 * 1024);
	if (pthread_create(&poll_thr, &poll_attr, polling_thread_main, NULL) != 0) {
		pthread_attr_destroy(&poll_attr);
		log_error("Failed to create polling thread");
		tg_api_cleanup();
		return 1;
	}
	pthread_attr_destroy(&poll_attr);

	log_info("ccgram %s started", CCGRAM_VERSION);

	int64_t offset = 0;
	while (g_running) {
		struct tg_update *updates = NULL;
		size_t count = 0;
		struct tg_error err;

		if (tg_get_updates(offset, 30, &updates, &count, &err) != 0) {
			if (!g_running)
				break;
			if (err.code == 409) {
				log_error("Another bot instance is polling with the same token (409 Conflict). Shutting down.");
				g_running = 0;
				break;
			}
			log_warn("getUpdates error (%d): %s", err.code, err.description);
			sleep(2);
			continue;
		}

		for (size_t i = 0; i < count; i++) {
			if (updates[i].update_id >= offset)
				offset = updates[i].update_id + 1;
			handlers_dispatch_update(&updates[i]);
			tg_update_free(&updates[i]);
		}
		free(updates);
	}

	log_info("Shutting down...");
	poll_wake_shutdown();
	pthread_join(poll_thr, NULL);
	diff_reset();
	router_reset();
	topic_tail_reset();
	tg_api_cleanup();
	return 0;
}
