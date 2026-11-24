#include "ccgram.h"

#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#define MAX_SIDECAR 128

struct sidecar_entry {
	char window_id[192];
	char pane_id[32];
	char marker[64];
};

static pthread_mutex_t g_tmux_mu = PTHREAD_MUTEX_INITIALIZER;
static struct sidecar_entry g_sidecars[MAX_SIDECAR];
static size_t g_sidecar_count = 0;

void tmux_init(void)
{
	pthread_mutex_lock(&g_tmux_mu);
	g_sidecar_count = 0;
	pthread_mutex_unlock(&g_tmux_mu);
}

/* Execute command via fork+execvp with 5s timeout; captures stdout & stderr */
static int run_cmd_capture(const char *const argv[], int timeout_ms,
			   char **out_stdout, char **out_stderr)
{
	int out_pipe[2] = { -1, -1 };
	int err_pipe[2] = { -1, -1 };
	pid_t pid;
	struct strbuf sb_out, sb_err;
	double deadline;
	int status = 0;
	int rc = -1;

	if (pipe(out_pipe) != 0 || pipe(err_pipe) != 0) {
		if (out_pipe[0] >= 0) { close(out_pipe[0]); close(out_pipe[1]); }
		if (err_pipe[0] >= 0) { close(err_pipe[0]); close(err_pipe[1]); }
		return -1;
	}

	pid = fork();
	if (pid < 0) {
		close(out_pipe[0]); close(out_pipe[1]);
		close(err_pipe[0]); close(err_pipe[1]);
		return -1;
	}

	if (pid == 0) {
		dup2(out_pipe[1], STDOUT_FILENO);
		dup2(err_pipe[1], STDERR_FILENO);
		close(out_pipe[0]); close(out_pipe[1]);
		close(err_pipe[0]); close(err_pipe[1]);
		execvp(argv[0], (char *const *)argv);
		_exit(127);
	}

	close(out_pipe[1]);
	close(err_pipe[1]);
	fcntl(out_pipe[0], F_SETFL, O_NONBLOCK);
	fcntl(err_pipe[0], F_SETFL, O_NONBLOCK);

	sb_init(&sb_out);
	sb_init(&sb_err);
	deadline = mono_now() + (double)timeout_ms / 1000.0;

	while (out_pipe[0] >= 0 || err_pipe[0] >= 0) {
		struct pollfd pfds[2];
		nfds_t nfds = 0;
		int rem_ms = (int)((deadline - mono_now()) * 1000.0);

		if (rem_ms <= 0) {
			kill(pid, SIGKILL);
			break;
		}
		if (out_pipe[0] >= 0) {
			pfds[nfds].fd = out_pipe[0];
			pfds[nfds].events = POLLIN;
			nfds++;
		}
		if (err_pipe[0] >= 0) {
			pfds[nfds].fd = err_pipe[0];
			pfds[nfds].events = POLLIN;
			nfds++;
		}
		int pr = poll(pfds, nfds, rem_ms);
		if (pr < 0) {
			if (errno == EINTR)
				continue;
			kill(pid, SIGKILL);
			break;
		}
		if (pr == 0) {
			kill(pid, SIGKILL);
			break;
		}
		for (nfds_t i = 0; i < nfds; i++) {
			char buf[4096];
			ssize_t n;
			int fd = pfds[i].fd;

			if (pfds[i].revents & (POLLIN | POLLHUP | POLLERR | POLLNVAL)) {
				while ((n = read(fd, buf, sizeof(buf))) > 0) {
					if (fd == out_pipe[0])
						sb_append(&sb_out, buf, (size_t)n);
					else
						sb_append(&sb_err, buf, (size_t)n);
				}
				if (n == 0 || (n < 0 && errno != EAGAIN && errno != EWOULDBLOCK)) {
					close(fd);
					if (fd == out_pipe[0]) out_pipe[0] = -1;
					else err_pipe[0] = -1;
				}
			}
		}
	}

	if (out_pipe[0] >= 0) close(out_pipe[0]);
	if (err_pipe[0] >= 0) close(err_pipe[0]);

	while (waitpid(pid, &status, 0) < 0 && errno == EINTR)
		;
	if (WIFEXITED(status))
		rc = WEXITSTATUS(status);

	if (out_stdout)
		*out_stdout = sb_detach(&sb_out);
	else
		sb_free(&sb_out);

	if (out_stderr)
		*out_stderr = sb_detach(&sb_err);
	else
		sb_free(&sb_err);

	return rc;
}

static void split_qualified(const char *window_id, char *sess, size_t sess_cap,
			    char *wid, size_t wid_cap)
{
	const char *colon;

	if (window_id[0] != '@') {
		colon = strrchr(window_id, ':');
		if (colon) {
			size_t slen = (size_t)(colon - window_id);
			if (slen >= sess_cap)
				slen = sess_cap - 1;
			memcpy(sess, window_id, slen);
			sess[slen] = '\0';
			snprintf(wid, wid_cap, "%s", colon + 1);
			return;
		}
	}
	snprintf(sess, sess_cap, "%s", g_cfg.tmux_session_name);
	snprintf(wid, wid_cap, "%s", window_id);
}

void tmux_topic_session_name(const char *topic_name, char *out, size_t cap)
{
	snprintf(out, cap, "%s%s", g_cfg.tmux_session_prefix, topic_name);
}

void tmux_topic_name_from_session(const char *session_name, char *out, size_t cap)
{
	size_t plen = strlen(g_cfg.tmux_session_prefix);

	if (strncmp(session_name, g_cfg.tmux_session_prefix, plen) == 0)
		snprintf(out, cap, "%s", session_name + plen);
	else
		snprintf(out, cap, "%s", session_name);
}

static bool parse_topic_ref(const char *val, int64_t *chat_id, int64_t *thread_id)
{
	const char *colon;
	char *end1 = NULL, *end2 = NULL;

	if (!val || !*val)
		return false;
	colon = strchr(val, ':');
	if (!colon)
		return false;
	*chat_id = strtoll(val, &end1, 10);
	if (end1 != colon)
		return false;
	*thread_id = strtoll(colon + 1, &end2, 10);
	return end2 != (colon + 1) && (*end2 == '\0' || *end2 == '\n');
}

size_t tmux_list_sessions(struct tmux_window *out, size_t max_items)
{
	const char *argv[] = {
		"tmux", "list-sessions", "-F",
		"#{session_name}\t#{pane_current_path}\t#{@ccgram_topic}", NULL
	};
	char *stdout_str = NULL;
	size_t count = 0;
	char *save = NULL;
	char *line;

	if (run_cmd_capture(argv, 5000, &stdout_str, NULL) != 0 || !stdout_str) {
		free(stdout_str);
		return 0;
	}

	for (line = strtok_r(stdout_str, "\n", &save);
	     line && count < max_items;
	     line = strtok_r(NULL, "\n", &save)) {
		char *t1 = strchr(line, '\t');
		char *t2 = t1 ? strchr(t1 + 1, '\t') : NULL;
		const char *sname = line;
		const char *cwd = "";
		const char *topic = "";
		char *w_out = NULL;
		char first_wid[64] = {0};

		if (t1) {
			*t1 = '\0';
			cwd = t1 + 1;
		}
		if (t2) {
			*t2 = '\0';
			topic = t2 + 1;
		}
		if (!*sname)
			continue;

		const char *wargv[] = {
			"tmux", "list-windows", "-t", sname, "-F", "#{window_id}", NULL
		};
		if (run_cmd_capture(wargv, 5000, &w_out, NULL) != 0 || !w_out) {
			free(w_out);
			continue;
		}
		char *nl = strchr(w_out, '\n');
		if (nl)
			*nl = '\0';
		snprintf(first_wid, sizeof(first_wid), "%s", w_out);
		free(w_out);
		if (!*first_wid)
			continue;

		struct tmux_window *w = &out[count++];
		memset(w, 0, sizeof(*w));
		snprintf(w->window_id, sizeof(w->window_id), "%s:%s", sname, first_wid);
		snprintf(w->window_name, sizeof(w->window_name), "%s", sname);
		snprintf(w->cwd, sizeof(w->cwd), "%s", cwd);
		w->has_topic_ref = parse_topic_ref(topic, &w->topic_chat_id, &w->topic_thread_id);
	}

	free(stdout_str);
	return count;
}

bool tmux_set_session_topic(const char *session_name, int64_t chat_id, int64_t thread_id)
{
	char val[64];
	const char *argv[] = { "tmux", "set-option", "-t", session_name, CCGRAM_TOPIC_OPTION, val, NULL };
	char *err = NULL;
	int rc;

	snprintf(val, sizeof(val), "%lld:%lld", (long long)chat_id, (long long)thread_id);
	rc = run_cmd_capture(argv, 5000, NULL, &err);
	if (rc != 0)
		log_warn("Failed to set %s on %s: %s", CCGRAM_TOPIC_OPTION, session_name, err ? err : "");
	free(err);
	return rc == 0;
}

bool tmux_clear_session_topic(const char *session_name)
{
	const char *argv[] = { "tmux", "set-option", "-u", "-t", session_name, CCGRAM_TOPIC_OPTION, NULL };
	return run_cmd_capture(argv, 5000, NULL, NULL) == 0;
}

bool tmux_find_window_by_id(const char *window_id, struct tmux_window *out)
{
	char sname[128], wid[64];
	char *stdout_str = NULL;
	char *save = NULL;
	char *line;
	bool found = false;

	split_qualified(window_id, sname, sizeof(sname), wid, sizeof(wid));
	const char *argv[] = {
		"tmux", "list-windows", "-t", sname, "-F",
		"#{window_id}\t#{window_name}\t#{pane_current_path}", NULL
	};
	if (run_cmd_capture(argv, 5000, &stdout_str, NULL) != 0 || !stdout_str) {
		free(stdout_str);
		return false;
	}

	for (line = strtok_r(stdout_str, "\n", &save); line; line = strtok_r(NULL, "\n", &save)) {
		char *t1 = strchr(line, '\t');
		char *t2 = t1 ? strchr(t1 + 1, '\t') : NULL;
		if (!t1)
			continue;
		*t1 = '\0';
		if (t2)
			*t2 = '\0';
		if (strcmp(line, wid) == 0) {
			memset(out, 0, sizeof(*out));
			snprintf(out->window_id, sizeof(out->window_id), "%s", window_id);
			snprintf(out->window_name, sizeof(out->window_name), "%s",
				 (t1 + 1)[0] ? (t1 + 1) : sname);
			snprintf(out->cwd, sizeof(out->cwd), "%s", t2 ? (t2 + 1) : "");
			found = true;
			break;
		}
	}
	free(stdout_str);
	return found;
}

static void ensure_managed_window_size(const char *session_name, const char *wid)
{
	char target[200];
	snprintf(target, sizeof(target), "%s:%s", session_name, wid);
	const char *argv[] = { "tmux", "set-window-option", "-t", target, "window-size", "largest", NULL };
	run_cmd_capture(argv, 2000, NULL, NULL);
}

static char *rstrip_dup(char *s)
{
	size_t len;

	if (!s)
		return NULL;
	len = strlen(s);
	while (len > 0 && isspace((unsigned char)s[len - 1]))
		s[--len] = '\0';
	if (len == 0) {
		free(s);
		return NULL;
	}
	return s;
}

char *tmux_capture_pane(const char *window_id, bool with_ansi, bool include_history)
{
	char sname[128], wid[64];
	const char *argv[8];
	int idx = 0;
	char *stdout_str = NULL;

	split_qualified(window_id, sname, sizeof(sname), wid, sizeof(wid));
	if (strncmp(sname, g_cfg.tmux_session_prefix, strlen(g_cfg.tmux_session_prefix)) == 0)
		ensure_managed_window_size(sname, wid);

	argv[idx++] = "tmux";
	argv[idx++] = "capture-pane";
	if (with_ansi)
		argv[idx++] = "-e";
	argv[idx++] = "-p";
	if (include_history) {
		argv[idx++] = "-S";
		argv[idx++] = "-";
	}
	argv[idx++] = "-t";
	argv[idx++] = window_id;
	argv[idx] = NULL;

	if (run_cmd_capture(argv, 5000, &stdout_str, NULL) != 0) {
		free(stdout_str);
		return NULL;
	}
	return rstrip_dup(stdout_str);
}

char *tmux_capture_pane_by_id(const char *pane_id, bool with_ansi, bool include_history, const char *window_id)
{
	const char *argv[8];
	int idx = 0;
	char *stdout_str = NULL;

	if (window_id && *window_id) {
		struct pane_info panes[16];
		size_t np = tmux_list_panes(window_id, panes, 16);
		bool belongs = false;
		for (size_t i = 0; i < np; i++) {
			if (strcmp(panes[i].pane_id, pane_id) == 0) {
				belongs = true;
				break;
			}
		}
		if (!belongs)
			return NULL;
	}

	argv[idx++] = "tmux";
	argv[idx++] = "capture-pane";
	if (with_ansi)
		argv[idx++] = "-e";
	argv[idx++] = "-p";
	if (include_history) {
		argv[idx++] = "-S";
		argv[idx++] = "-";
	}
	argv[idx++] = "-t";
	argv[idx++] = pane_id;
	argv[idx] = NULL;

	if (run_cmd_capture(argv, 5000, &stdout_str, NULL) != 0) {
		free(stdout_str);
		return NULL;
	}
	return rstrip_dup(stdout_str);
}

bool tmux_send_keys(const char *window_id, const char *text, bool enter, bool literal)
{
	const char *argv[9];
	int idx = 0;
	int rc;

	argv[idx++] = "tmux";
	argv[idx++] = "send-keys";
	argv[idx++] = "-t";
	argv[idx++] = window_id;
	if (literal)
		argv[idx++] = "-l";
	argv[idx++] = "--";
	argv[idx++] = text;
	argv[idx] = NULL;

	rc = run_cmd_capture(argv, 5000, NULL, NULL);
	if (rc != 0)
		return false;

	if (enter) {
		const char *ent_argv[] = { "tmux", "send-keys", "-t", window_id, "Enter", NULL };
		rc = run_cmd_capture(ent_argv, 5000, NULL, NULL);
	}
	return rc == 0;
}

bool tmux_send_keys_to_pane(const char *pane_id, const char *text, bool enter, bool literal, const char *window_id)
{
	if (window_id && *window_id) {
		struct pane_info panes[16];
		size_t np = tmux_list_panes(window_id, panes, 16);
		bool belongs = false;
		for (size_t i = 0; i < np; i++) {
			if (strcmp(panes[i].pane_id, pane_id) == 0) {
				belongs = true;
				break;
			}
		}
		if (!belongs)
			return false;
	}
	return tmux_send_keys(pane_id, text, enter, literal);
}

bool tmux_send_to_window(const char *window_id, const char *text, char *err_buf, size_t err_cap)
{
	struct tmux_window w;

	if (!tmux_find_window_by_id(window_id, &w)) {
		snprintf(err_buf, err_cap, "Window not found");
		return false;
	}
	if (!tmux_send_keys(window_id, text, true, true)) {
		snprintf(err_buf, err_cap, "Failed to send keys to tmux");
		return false;
	}
	return true;
}

bool tmux_kill_session(const char *session_name)
{
	if (strcmp(session_name, g_cfg.tmux_session_name) == 0) {
		log_warn("Refusing to kill main tmux session %s", session_name);
		return false;
	}

	pthread_mutex_lock(&g_tmux_mu);
	size_t plen = strlen(session_name);
	for (size_t i = 0; i < g_sidecar_count;) {
		if (strncmp(g_sidecars[i].window_id, session_name, plen) == 0 &&
		    g_sidecars[i].window_id[plen] == ':')
			g_sidecars[i] = g_sidecars[--g_sidecar_count];
		else
			i++;
	}
	pthread_mutex_unlock(&g_tmux_mu);

	const char *argv[] = { "tmux", "kill-session", "-t", session_name, NULL };
	return run_cmd_capture(argv, 5000, NULL, NULL) == 0;
}

bool tmux_rename_session(const char *session_name, const char *new_name)
{
	const char *argv[] = { "tmux", "rename-session", "-t", session_name, new_name, NULL };
	return run_cmd_capture(argv, 5000, NULL, NULL) == 0;
}

bool tmux_session_exists(const char *session_name, char *err_buf, size_t err_cap)
{
	const char *argv[] = { "tmux", "has-session", "-t", session_name, NULL };
	char *err = NULL;
	int rc = run_cmd_capture(argv, 5000, NULL, &err);

	if (err_buf && err_cap > 0)
		snprintf(err_buf, err_cap, "%s", err ? err : "");
	free(err);
	return rc == 0;
}

bool tmux_switch_client(const char *session_name)
{
	const char *argv[] = { "tmux", "switch-client", "-t", session_name, NULL };
	return run_cmd_capture(argv, 5000, NULL, NULL) == 0;
}

static void expand_tilde(const char *in, char *out, size_t cap)
{
	if (in[0] == '~' && (in[1] == '/' || in[1] == '\0')) {
		const char *home = getenv("HOME");
		if (!home)
			home = ".";
		snprintf(out, cap, "%s%s", home, in + 1);
	} else {
		snprintf(out, cap, "%s", in);
	}
}

bool tmux_create_session(const char *session_name, const char *work_dir,
			 const char *window_name, char *out_wid, size_t wid_cap,
			 char *err_buf, size_t err_cap)
{
	char expanded[512];
	char *stdout_str = NULL;
	char *err = NULL;
	int rc;

	expand_tilde(work_dir, expanded, sizeof(expanded));
	if (!path_is_dir(expanded)) {
		snprintf(err_buf, err_cap, "Directory does not exist: %s", work_dir);
		return false;
	}

	const char *final_name = (window_name && *window_name) ? window_name : session_name;
	const char *argv[] = {
		"tmux", "new-session", "-d", "-s", session_name, "-n", final_name,
		"-c", expanded, "-P", "-F", "#{window_id}", NULL
	};
	rc = run_cmd_capture(argv, 5000, &stdout_str, &err);
	if (rc != 0 || !stdout_str || !*stdout_str) {
		snprintf(err_buf, err_cap, "%s", err ? err : "new-session failed");
		free(stdout_str);
		free(err);
		return false;
	}
	free(err);

	char *nl = strchr(stdout_str, '\n');
	if (nl)
		*nl = '\0';
	snprintf(out_wid, wid_cap, "%s:%s", session_name, stdout_str);
	free(stdout_str);

	const char *opt_argv[] = { "tmux", "set-window-option", "-t", out_wid, "automatic-rename", "off", NULL };
	run_cmd_capture(opt_argv, 2000, NULL, NULL);
	return true;
}

size_t tmux_list_panes(const char *window_id, struct pane_info *out, size_t max_items)
{
	const char *argv[] = {
		"tmux", "list-panes", "-t", window_id, "-F",
		"#{pane_id}\t#{pane_active}\t#{pane_current_path}\t#{pane_dead}", NULL
	};
	char *stdout_str = NULL;
	char *save = NULL;
	char *line;
	size_t count = 0;

	if (run_cmd_capture(argv, 5000, &stdout_str, NULL) != 0 || !stdout_str) {
		free(stdout_str);
		return 0;
	}

	for (line = strtok_r(stdout_str, "\n", &save);
	     line && count < max_items;
	     line = strtok_r(NULL, "\n", &save)) {
		char *t1 = strchr(line, '\t');
		char *t2 = t1 ? strchr(t1 + 1, '\t') : NULL;
		char *t3 = t2 ? strchr(t2 + 1, '\t') : NULL;
		if (!t1 || !t2 || !t3)
			continue;
		*t1 = '\0'; *t2 = '\0'; *t3 = '\0';
		struct pane_info *p = &out[count++];
		snprintf(p->pane_id, sizeof(p->pane_id), "%s", line);
		p->active = strcmp(t1 + 1, "1") == 0;
		snprintf(p->path, sizeof(p->path), "%s", t2 + 1);
		p->dead = strcmp(t3 + 1, "1") == 0;
	}
	free(stdout_str);
	return count;
}

bool tmux_kill_pane(const char *pane_id, const char *window_id)
{
	if (window_id && *window_id) {
		struct pane_info panes[16];
		size_t np = tmux_list_panes(window_id, panes, 16);
		bool belongs = false;
		for (size_t i = 0; i < np; i++) {
			if (strcmp(panes[i].pane_id, pane_id) == 0) {
				belongs = true;
				break;
			}
		}
		if (!belongs)
			return false;
	}
	const char *argv[] = { "tmux", "kill-pane", "-t", pane_id, NULL };
	return run_cmd_capture(argv, 5000, NULL, NULL) == 0;
}

bool tmux_get_sidecar_pane_id(const char *window_id, char *out, size_t cap)
{
	bool found = false;

	pthread_mutex_lock(&g_tmux_mu);
	for (size_t i = 0; i < g_sidecar_count; i++) {
		if (strcmp(g_sidecars[i].window_id, window_id) == 0) {
			snprintf(out, cap, "%s", g_sidecars[i].pane_id);
			found = true;
			break;
		}
	}
	pthread_mutex_unlock(&g_tmux_mu);
	return found;
}

bool tmux_get_sidecar_marker(const char *window_id, char *out, size_t cap)
{
	bool found = false;

	pthread_mutex_lock(&g_tmux_mu);
	for (size_t i = 0; i < g_sidecar_count; i++) {
		if (strcmp(g_sidecars[i].window_id, window_id) == 0) {
			snprintf(out, cap, "%s", g_sidecars[i].marker);
			found = true;
			break;
		}
	}
	pthread_mutex_unlock(&g_tmux_mu);
	return found;
}

void tmux_forget_sidecar_pane(const char *window_id)
{
	pthread_mutex_lock(&g_tmux_mu);
	for (size_t i = 0; i < g_sidecar_count; i++) {
		if (strcmp(g_sidecars[i].window_id, window_id) == 0) {
			g_sidecars[i] = g_sidecars[--g_sidecar_count];
			break;
		}
	}
	pthread_mutex_unlock(&g_tmux_mu);
}

static void shell_quote(const char *in, struct strbuf *sb)
{
	sb_append_str(sb, "'");
	for (const char *p = in; *p; p++) {
		if (*p == '\'')
			sb_append_str(sb, "'\"'\"'");
		else
			sb_append(sb, p, 1);
	}
	sb_append_str(sb, "'");
}

bool tmux_start_sidecar_command(const char *window_id, const char *command,
				char *out_pane_id, size_t pid_cap,
				char *out_marker, size_t m_cap)
{
	char tracked[32];
	struct pane_info panes[16];
	size_t np;
	const char *cwd = NULL;
	char marker[64];
	struct strbuf wrapped;
	char *stdout_str = NULL;
	int rc;

	if (tmux_get_sidecar_pane_id(window_id, tracked, sizeof(tracked))) {
		np = tmux_list_panes(window_id, panes, 16);
		bool alive = false, found = false;
		for (size_t i = 0; i < np; i++) {
			if (strcmp(panes[i].pane_id, tracked) == 0) {
				found = true;
				alive = !panes[i].dead;
				break;
			}
		}
		if (found && alive)
			return false;
		if (found)
			tmux_kill_pane(tracked, window_id);
		tmux_forget_sidecar_pane(window_id);
	}

	np = tmux_list_panes(window_id, panes, 16);
	if (np == 0)
		return false;
	cwd = panes[0].path;
	for (size_t i = 0; i < np; i++) {
		if (panes[i].active) {
			cwd = panes[i].path;
			break;
		}
	}
	if (!cwd || !path_is_dir(cwd))
		return false;

	snprintf(marker, sizeof(marker), "__CCG_DONE_%08llx__", (unsigned long long)(time_ns_now() & 0xffffffffULL));

	sb_init(&wrapped);
	sb_append_str(&wrapped, "tmux set-option -p -t \"$TMUX_PANE\" remain-on-exit on 2>/dev/null || true; ");
	sb_appendf(&wrapped, "printf '\\n%%s:BEGIN\\n' '%s'; ", marker);
	sb_append_str(&wrapped, "/bin/sh -c ");
	shell_quote(command, &wrapped);
	sb_appendf(&wrapped, "; _ccgram_status=$?; printf '\\n%%s:%%s\\n' '%s' \"$_ccgram_status\"", marker);

	const char *argv[] = {
		"tmux", "split-window", "-d", "-t", window_id, "-c", cwd,
		"-P", "-F", "#{pane_id}", "/bin/sh", "-c", wrapped.data, NULL
	};
	rc = run_cmd_capture(argv, 5000, &stdout_str, NULL);
	sb_free(&wrapped);

	if (rc != 0 || !stdout_str || !*stdout_str) {
		free(stdout_str);
		return false;
	}
	char *nl = strchr(stdout_str, '\n');
	if (nl)
		*nl = '\0';

	pthread_mutex_lock(&g_tmux_mu);
	if (g_sidecar_count < MAX_SIDECAR) {
		snprintf(g_sidecars[g_sidecar_count].window_id, sizeof(g_sidecars[0].window_id), "%s", window_id);
		snprintf(g_sidecars[g_sidecar_count].pane_id, sizeof(g_sidecars[0].pane_id), "%s", stdout_str);
		snprintf(g_sidecars[g_sidecar_count].marker, sizeof(g_sidecars[0].marker), "%s", marker);
		g_sidecar_count++;
	}
	pthread_mutex_unlock(&g_tmux_mu);

	snprintf(out_pane_id, pid_cap, "%s", stdout_str);
	snprintf(out_marker, m_cap, "%s", marker);
	free(stdout_str);
	return true;
}

bool extract_sidecar_output(const char *text, const char *marker, char **out_text, bool *out_completed)
{
	char begin_marker[80];
	size_t mlen = strlen(marker);
	const char *p = text;
	const char *start = NULL;
	const char *end = NULL;
	struct strbuf sb;

	*out_text = NULL;
	*out_completed = false;
	if (!text || !marker)
		return false;

	snprintf(begin_marker, sizeof(begin_marker), "%s:BEGIN", marker);

	while (*p) {
		const char *nl = strchr(p, '\n');
		size_t llen = nl ? (size_t)(nl - p) : strlen(p);
		size_t tlen = llen;
		while (tlen > 0 && isspace((unsigned char)p[tlen - 1]))
			tlen--;
		const char *ts = p;
		while (tlen > 0 && isspace((unsigned char)*ts)) {
			ts++;
			tlen--;
		}
		if (!start) {
			if (tlen == strlen(begin_marker) && strncmp(ts, begin_marker, tlen) == 0)
				start = nl ? nl + 1 : p + llen;
		} else {
			if (tlen > mlen + 1 && strncmp(ts, marker, mlen) == 0 && ts[mlen] == ':') {
				bool all_digits = true;
				for (size_t i = mlen + 1; i < tlen; i++) {
					if (!isdigit((unsigned char)ts[i])) {
						all_digits = false;
						break;
					}
				}
				if (all_digits) {
					end = p;
					*out_completed = true;
					break;
				}
			}
		}
		if (!nl)
			break;
		p = nl + 1;
	}

	if (!start)
		return false;

	sb_init(&sb);
	if (end && end > start)
		sb_append(&sb, start, (size_t)(end - start));
	else if (!end)
		sb_append_str(&sb, start);

	while (sb.len > 0 && isspace((unsigned char)sb.data[sb.len - 1]))
		sb.data[--sb.len] = '\0';

	*out_text = sb_detach(&sb);
	return true;
}
