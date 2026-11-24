#include "ccgram.h"

#include <stdio.h>
#include <string.h>

#define MAX_BINDINGS 256
#define MAX_DISPLAY_NAMES 256
#define MAX_TAIL_TOPICS 512
#define MAX_RENAME_STATES 64

struct group_chat_entry {
	int64_t user_id;
	int64_t thread_id;
	int64_t chat_id;
};

struct display_entry {
	char window_id[192];
	char name[128];
};

struct tail_entry {
	int64_t chat_id;
	int64_t thread_id;
	int64_t last_msg_id;
};

struct rename_entry {
	int64_t user_id;
	int64_t thread_id;
	char window_id[192];
};

static pthread_mutex_t g_router_mu = PTHREAD_MUTEX_INITIALIZER;
static struct thread_binding g_bindings[MAX_BINDINGS];
static size_t g_binding_count = 0;
static struct group_chat_entry g_chats[MAX_BINDINGS];
static size_t g_chat_count = 0;
static struct display_entry g_names[MAX_DISPLAY_NAMES];
static size_t g_name_count = 0;

static pthread_mutex_t g_tail_mu = PTHREAD_MUTEX_INITIALIZER;
static struct tail_entry g_tails[MAX_TAIL_TOPICS];
static size_t g_tail_count = 0;

static pthread_mutex_t g_state_mu = PTHREAD_MUTEX_INITIALIZER;
static struct rename_entry g_renames[MAX_RENAME_STATES];
static size_t g_rename_count = 0;

void router_init(void)
{
	router_reset();
}

void router_reset(void)
{
	pthread_mutex_lock(&g_router_mu);
	g_binding_count = 0;
	g_chat_count = 0;
	g_name_count = 0;
	pthread_mutex_unlock(&g_router_mu);
}

static void set_display_name_locked(const char *window_id, const char *name)
{
	if (!name || !*name)
		return;
	for (size_t i = 0; i < g_name_count; i++) {
		if (strcmp(g_names[i].window_id, window_id) == 0) {
			snprintf(g_names[i].name, sizeof(g_names[i].name), "%s", name);
			return;
		}
	}
	if (g_name_count < MAX_DISPLAY_NAMES) {
		snprintf(g_names[g_name_count].window_id, sizeof(g_names[0].window_id), "%s", window_id);
		snprintf(g_names[g_name_count].name, sizeof(g_names[0].name), "%s", name);
		g_name_count++;
	}
}

void router_bind_thread(int64_t user_id, int64_t thread_id, const char *window_id, const char *window_name)
{
	pthread_mutex_lock(&g_router_mu);

	/* Enforce 1:1 — evict any OTHER thread for this user pointing to window_id */
	for (size_t i = 0; i < g_binding_count;) {
		if (g_bindings[i].user_id == user_id &&
		    strcmp(g_bindings[i].window_id, window_id) == 0 &&
		    g_bindings[i].thread_id != thread_id) {
			log_info("Evicted stale tg_topic binding: thread %lld -> %s (replaced by %lld)",
				 (long long)g_bindings[i].thread_id, window_id, (long long)thread_id);
			g_bindings[i] = g_bindings[--g_binding_count];
		} else {
			i++;
		}
	}

	bool found = false;
	for (size_t i = 0; i < g_binding_count; i++) {
		if (g_bindings[i].user_id == user_id && g_bindings[i].thread_id == thread_id) {
			snprintf(g_bindings[i].window_id, sizeof(g_bindings[i].window_id), "%s", window_id);
			found = true;
			break;
		}
	}
	if (!found && g_binding_count < MAX_BINDINGS) {
		g_bindings[g_binding_count].user_id = user_id;
		g_bindings[g_binding_count].thread_id = thread_id;
		snprintf(g_bindings[g_binding_count].window_id, sizeof(g_bindings[0].window_id), "%s", window_id);
		g_binding_count++;
	}

	set_display_name_locked(window_id, window_name);
	pthread_mutex_unlock(&g_router_mu);

	log_info("Bound tg_topic %lld -> tmux_session %s (%s) for user %lld",
		 (long long)thread_id, window_id, window_name ? window_name : window_id, (long long)user_id);
}

bool router_unbind_thread(int64_t user_id, int64_t thread_id, char *prev_wid, size_t prev_cap)
{
	char old_wid[192] = {0};
	bool removed = false;

	pthread_mutex_lock(&g_router_mu);
	for (size_t i = 0; i < g_binding_count; i++) {
		if (g_bindings[i].user_id == user_id && g_bindings[i].thread_id == thread_id) {
			snprintf(old_wid, sizeof(old_wid), "%s", g_bindings[i].window_id);
			g_bindings[i] = g_bindings[--g_binding_count];
			removed = true;
			break;
		}
	}
	if (!removed) {
		pthread_mutex_unlock(&g_router_mu);
		return false;
	}

	for (size_t i = 0; i < g_chat_count; i++) {
		if (g_chats[i].user_id == user_id && g_chats[i].thread_id == thread_id) {
			g_chats[i] = g_chats[--g_chat_count];
			break;
		}
	}

	bool still_bound = false;
	for (size_t i = 0; i < g_binding_count; i++) {
		if (strcmp(g_bindings[i].window_id, old_wid) == 0) {
			still_bound = true;
			break;
		}
	}
	if (!still_bound) {
		for (size_t i = 0; i < g_name_count; i++) {
			if (strcmp(g_names[i].window_id, old_wid) == 0) {
				g_names[i] = g_names[--g_name_count];
				break;
			}
		}
	}
	pthread_mutex_unlock(&g_router_mu);

	if (prev_wid && prev_cap > 0)
		snprintf(prev_wid, prev_cap, "%s", old_wid);
	log_info("Unbound tg_topic %lld (was %s) for user %lld",
		 (long long)thread_id, old_wid, (long long)user_id);
	return true;
}

bool router_get_window_for_thread(int64_t user_id, int64_t thread_id, char *out, size_t cap)
{
	bool found = false;

	pthread_mutex_lock(&g_router_mu);
	for (size_t i = 0; i < g_binding_count; i++) {
		if (g_bindings[i].user_id == user_id && g_bindings[i].thread_id == thread_id) {
			snprintf(out, cap, "%s", g_bindings[i].window_id);
			found = true;
			break;
		}
	}
	pthread_mutex_unlock(&g_router_mu);
	return found;
}

size_t router_get_all_thread_windows(int64_t user_id, int64_t *out_tids, char (*out_wids)[192], size_t max_items)
{
	size_t n = 0;

	pthread_mutex_lock(&g_router_mu);
	for (size_t i = 0; i < g_binding_count && n < max_items; i++) {
		if (g_bindings[i].user_id == user_id) {
			if (out_tids)
				out_tids[n] = g_bindings[i].thread_id;
			if (out_wids)
				snprintf(out_wids[n], 192, "%s", g_bindings[i].window_id);
			n++;
		}
	}
	pthread_mutex_unlock(&g_router_mu);
	return n;
}

size_t router_iter_bindings(struct thread_binding *out, size_t max_items)
{
	size_t n;

	pthread_mutex_lock(&g_router_mu);
	n = g_binding_count < max_items ? g_binding_count : max_items;
	if (n > 0)
		memcpy(out, g_bindings, n * sizeof(struct thread_binding));
	pthread_mutex_unlock(&g_router_mu);
	return n;
}

void router_set_group_chat_id(int64_t user_id, int64_t thread_id, int64_t chat_id)
{
	pthread_mutex_lock(&g_router_mu);
	for (size_t i = 0; i < g_chat_count; i++) {
		if (g_chats[i].user_id == user_id && g_chats[i].thread_id == thread_id) {
			g_chats[i].chat_id = chat_id;
			pthread_mutex_unlock(&g_router_mu);
			return;
		}
	}
	if (g_chat_count < MAX_BINDINGS) {
		g_chats[g_chat_count].user_id = user_id;
		g_chats[g_chat_count].thread_id = thread_id;
		g_chats[g_chat_count].chat_id = chat_id;
		g_chat_count++;
	}
	pthread_mutex_unlock(&g_router_mu);
}

int64_t router_resolve_chat_id(int64_t user_id, int64_t thread_id)
{
	int64_t res = user_id;

	pthread_mutex_lock(&g_router_mu);
	for (size_t i = 0; i < g_chat_count; i++) {
		if (g_chats[i].user_id == user_id && g_chats[i].thread_id == thread_id) {
			res = g_chats[i].chat_id;
			break;
		}
	}
	pthread_mutex_unlock(&g_router_mu);
	return res;
}

bool router_get_window_for_chat_thread(int64_t chat_id, int64_t thread_id, char *out, size_t cap)
{
	bool found = false;

	pthread_mutex_lock(&g_router_mu);
	for (size_t i = 0; i < g_binding_count; i++) {
		if (g_bindings[i].thread_id != thread_id)
			continue;
		int64_t resolved = g_bindings[i].user_id;
		for (size_t j = 0; j < g_chat_count; j++) {
			if (g_chats[j].user_id == g_bindings[i].user_id &&
			    g_chats[j].thread_id == thread_id) {
				resolved = g_chats[j].chat_id;
				break;
			}
		}
		if (resolved == chat_id) {
			snprintf(out, cap, "%s", g_bindings[i].window_id);
			found = true;
			break;
		}
	}
	pthread_mutex_unlock(&g_router_mu);
	return found;
}

void router_get_display_name(const char *window_id, char *out, size_t cap)
{
	pthread_mutex_lock(&g_router_mu);
	for (size_t i = 0; i < g_name_count; i++) {
		if (strcmp(g_names[i].window_id, window_id) == 0) {
			snprintf(out, cap, "%s", g_names[i].name);
			pthread_mutex_unlock(&g_router_mu);
			return;
		}
	}
	pthread_mutex_unlock(&g_router_mu);
	snprintf(out, cap, "%s", window_id);
}

bool router_user_owns_window(int64_t user_id, const char *window_id)
{
	bool owns = false;

	pthread_mutex_lock(&g_router_mu);
	for (size_t i = 0; i < g_binding_count; i++) {
		if (g_bindings[i].user_id == user_id &&
		    strcmp(g_bindings[i].window_id, window_id) == 0) {
			owns = true;
			break;
		}
	}
	pthread_mutex_unlock(&g_router_mu);
	return owns;
}

/* ── Topic Tail ────────────────────────────────────────────────────── */

void topic_tail_record(int64_t chat_id, int64_t thread_id, int64_t message_id)
{
	if (thread_id <= 0 || message_id <= 0)
		return;
	pthread_mutex_lock(&g_tail_mu);
	for (size_t i = 0; i < g_tail_count; i++) {
		if (g_tails[i].chat_id == chat_id && g_tails[i].thread_id == thread_id) {
			if (message_id > g_tails[i].last_msg_id)
				g_tails[i].last_msg_id = message_id;
			pthread_mutex_unlock(&g_tail_mu);
			return;
		}
	}
	if (g_tail_count < MAX_TAIL_TOPICS) {
		g_tails[g_tail_count].chat_id = chat_id;
		g_tails[g_tail_count].thread_id = thread_id;
		g_tails[g_tail_count].last_msg_id = message_id;
		g_tail_count++;
	}
	pthread_mutex_unlock(&g_tail_mu);
}

bool topic_tail_is_last(int64_t chat_id, int64_t thread_id, int64_t message_id)
{
	bool res = true;

	pthread_mutex_lock(&g_tail_mu);
	for (size_t i = 0; i < g_tail_count; i++) {
		if (g_tails[i].chat_id == chat_id && g_tails[i].thread_id == thread_id) {
			res = message_id >= g_tails[i].last_msg_id;
			break;
		}
	}
	pthread_mutex_unlock(&g_tail_mu);
	return res;
}

void topic_tail_clear(int64_t chat_id, int64_t thread_id)
{
	pthread_mutex_lock(&g_tail_mu);
	for (size_t i = 0; i < g_tail_count; i++) {
		if (g_tails[i].chat_id == chat_id && g_tails[i].thread_id == thread_id) {
			g_tails[i] = g_tails[--g_tail_count];
			break;
		}
	}
	pthread_mutex_unlock(&g_tail_mu);
}

void topic_tail_reset(void)
{
	pthread_mutex_lock(&g_tail_mu);
	g_tail_count = 0;
	pthread_mutex_unlock(&g_tail_mu);
}

/* ── User State (Pending Rename) ───────────────────────────────────── */

void user_state_set_rename(int64_t user_id, int64_t thread_id, const char *window_id)
{
	pthread_mutex_lock(&g_state_mu);
	for (size_t i = 0; i < g_rename_count; i++) {
		if (g_renames[i].user_id == user_id) {
			g_renames[i].thread_id = thread_id;
			snprintf(g_renames[i].window_id, sizeof(g_renames[i].window_id), "%s", window_id);
			pthread_mutex_unlock(&g_state_mu);
			return;
		}
	}
	if (g_rename_count < MAX_RENAME_STATES) {
		g_renames[g_rename_count].user_id = user_id;
		g_renames[g_rename_count].thread_id = thread_id;
		snprintf(g_renames[g_rename_count].window_id, sizeof(g_renames[0].window_id), "%s", window_id);
		g_rename_count++;
	}
	pthread_mutex_unlock(&g_state_mu);
}

bool user_state_pop_rename(int64_t user_id, int64_t thread_id, char *out_wid, size_t cap, int64_t *out_tid)
{
	bool found = false;

	pthread_mutex_lock(&g_state_mu);
	for (size_t i = 0; i < g_rename_count; i++) {
		if (g_renames[i].user_id == user_id) {
			if (g_renames[i].thread_id > 0 && g_renames[i].thread_id != thread_id) {
				pthread_mutex_unlock(&g_state_mu);
				return false;
			}
			if (out_tid)
				*out_tid = g_renames[i].thread_id;
			snprintf(out_wid, cap, "%s", g_renames[i].window_id);
			g_renames[i] = g_renames[--g_rename_count];
			found = true;
			break;
		}
	}
	pthread_mutex_unlock(&g_state_mu);
	return found;
}
