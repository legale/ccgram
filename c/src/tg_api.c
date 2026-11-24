#include "ccgram.h"

#include <curl/curl.h>
#include <json-c/json.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define MAX_RATE_CHATS 256
#define MESSAGE_SEND_INTERVAL 0.5
#define MAX_RETRY_AFTER_S 10

struct rate_entry {
	int64_t chat_id;
	double last_send_ts;
};

static pthread_mutex_t g_rate_mu = PTHREAD_MUTEX_INITIALIZER;
static struct rate_entry g_rates[MAX_RATE_CHATS];
static size_t g_rate_count = 0;

void tg_api_init(void)
{
	curl_global_init(CURL_GLOBAL_ALL);
}

void tg_api_cleanup(void)
{
	curl_global_cleanup();
}

void tg_update_free(struct tg_update *u)
{
	if (!u)
		return;
	free(u->text);
	free(u->caption);
	u->text = NULL;
	u->caption = NULL;
}

bool tg_is_thread_gone(const struct tg_error *err)
{
	char lower[256];

	if (!err || err->code != 400)
		return false;
	snprintf(lower, sizeof(lower), "%s", err->description);
	for (char *p = lower; *p; p++) {
		if (*p >= 'A' && *p <= 'Z')
			*p = (char)(*p + ('a' - 'A'));
	}
	return strstr(lower, "thread not found") != NULL ||
	       strstr(lower, "topic_id_invalid") != NULL;
}

void tg_rate_limit_send(int64_t chat_id)
{
	double now, target, wait_s;
	struct rate_entry *e = NULL;

	pthread_mutex_lock(&g_rate_mu);
	now = mono_now();
	for (size_t i = 0; i < g_rate_count; i++) {
		if (g_rates[i].chat_id == chat_id) {
			e = &g_rates[i];
			break;
		}
	}
	if (!e && g_rate_count < MAX_RATE_CHATS) {
		e = &g_rates[g_rate_count++];
		e->chat_id = chat_id;
		e->last_send_ts = 0.0;
	}
	if (e && e->last_send_ts > 0.0) {
		target = e->last_send_ts + MESSAGE_SEND_INTERVAL;
		if (target > now) {
			wait_s = target - now;
			e->last_send_ts = target;
			pthread_mutex_unlock(&g_rate_mu);
			struct timespec ts = {
				.tv_sec = (time_t)wait_s,
				.tv_nsec = (long)((wait_s - (double)(time_t)wait_s) * 1e9)
			};
			nanosleep(&ts, NULL);
			return;
		}
	}
	if (e)
		e->last_send_ts = now;
	pthread_mutex_unlock(&g_rate_mu);
}

static size_t curl_write_cb(void *ptr, size_t size, size_t nmemb, void *userdata)
{
	struct strbuf *sb = userdata;
	size_t total = size * nmemb;

	if (!sb_append(sb, ptr, total))
		return 0;
	return total;
}

static int curl_xfer_cb(void *clientp, curl_off_t dltotal, curl_off_t dlnow,
			curl_off_t ultotal, curl_off_t ulnow)
{
	(void)clientp; (void)dltotal; (void)dlnow; (void)ultotal; (void)ulnow;
	return g_running ? 0 : 1;
}

static void fill_tg_error(json_object *root, struct tg_error *err)
{
	json_object *jcode = NULL, *jdesc = NULL, *jparams = NULL, *jra = NULL;

	if (!err)
		return;
	memset(err, 0, sizeof(*err));
	if (!root)
		return;
	if (json_object_object_get_ex(root, "error_code", &jcode))
		err->code = json_object_get_int(jcode);
	if (json_object_object_get_ex(root, "description", &jdesc)) {
		const char *d = json_object_get_string(jdesc);
		if (d)
			snprintf(err->description, sizeof(err->description), "%s", d);
	}
	if (json_object_object_get_ex(root, "parameters", &jparams) &&
	    json_object_object_get_ex(jparams, "retry_after", &jra)) {
		err->retry_after = json_object_get_int(jra);
	}
}

static json_object *tg_post_json(const char *method, json_object *payload,
				 int timeout_s, struct tg_error *err)
{
	CURL *curl;
	char url[512];
	struct strbuf resp;
	struct curl_slist *hdrs = NULL;
	const char *body_str;
	CURLcode rc;
	json_object *root = NULL;
	json_object *jok = NULL;

	if (err)
		memset(err, 0, sizeof(*err));

	curl = curl_easy_init();
	if (!curl)
		return NULL;

	snprintf(url, sizeof(url), "https://api.telegram.org/bot%s/%s",
		 g_cfg.telegram_bot_token, method);

	sb_init(&resp);
	body_str = json_object_to_json_string_ext(payload, JSON_C_TO_STRING_PLAIN);
	hdrs = curl_slist_append(hdrs, "Content-Type: application/json");

	curl_easy_setopt(curl, CURLOPT_URL, url);
	curl_easy_setopt(curl, CURLOPT_HTTPHEADER, hdrs);
	curl_easy_setopt(curl, CURLOPT_POSTFIELDS, body_str);
	curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, curl_write_cb);
	curl_easy_setopt(curl, CURLOPT_WRITEDATA, &resp);
	curl_easy_setopt(curl, CURLOPT_TIMEOUT, (long)timeout_s);
	curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT, 10L);
	curl_easy_setopt(curl, CURLOPT_NOPROGRESS, 0L);
	curl_easy_setopt(curl, CURLOPT_XFERINFOFUNCTION, curl_xfer_cb);

	rc = curl_easy_perform(curl);
	curl_slist_free_all(hdrs);
	curl_easy_cleanup(curl);

	if (rc != CURLE_OK) {
		if (err) {
			err->code = -1;
			snprintf(err->description, sizeof(err->description), "%s", curl_easy_strerror(rc));
		}
		sb_free(&resp);
		return NULL;
	}

	root = json_tokener_parse(resp.data ? resp.data : "");
	sb_free(&resp);
	if (!root) {
		if (err) {
			err->code = -2;
			snprintf(err->description, sizeof(err->description), "Invalid JSON response");
		}
		return NULL;
	}

	if (!json_object_object_get_ex(root, "ok", &jok) || !json_object_get_boolean(jok)) {
		fill_tg_error(root, err);
		json_object_put(root);
		return NULL;
	}

	return root;
}

static json_object *build_keyboard_json(const struct tg_keyboard *kb)
{
	json_object *markup, *inline_kb;

	if (!kb || kb->num_rows == 0)
		return NULL;

	markup = json_object_new_object();
	inline_kb = json_object_new_array();

	for (size_t r = 0; r < kb->num_rows; r++) {
		json_object *row = json_object_new_array();
		for (size_t c = 0; c < kb->row_lens[r]; c++) {
			json_object *btn = json_object_new_object();
			json_object_object_add(btn, "text", json_object_new_string(kb->rows[r][c].text));
			json_object_object_add(btn, "callback_data", json_object_new_string(kb->rows[r][c].callback_data));
			json_object_array_add(row, btn);
		}
		json_object_array_add(inline_kb, row);
	}
	json_object_object_add(markup, "inline_keyboard", inline_kb);
	return markup;
}

static json_object *build_entities_json(const struct tg_entity *ents, size_t count)
{
	json_object *arr = json_object_new_array();

	for (size_t i = 0; i < count; i++) {
		json_object *e = json_object_new_object();
		json_object_object_add(e, "type", json_object_new_string(ents[i].type));
		json_object_object_add(e, "offset", json_object_new_int(ents[i].offset));
		json_object_object_add(e, "length", json_object_new_int(ents[i].length));
		if (ents[i].url[0])
			json_object_object_add(e, "url", json_object_new_string(ents[i].url));
		if (ents[i].language[0])
			json_object_object_add(e, "language", json_object_new_string(ents[i].language));
		json_object_array_add(arr, e);
	}
	return arr;
}

static void add_no_link_preview(json_object *obj)
{
	json_object *lpo = json_object_new_object();
	json_object_object_add(lpo, "is_disabled", json_object_new_boolean(1));
	json_object_object_add(obj, "link_preview_options", lpo);
}

static void json_copy_str(json_object *obj, const char *key, char *out, size_t cap)
{
	json_object *j = NULL;
	const char *s;

	if (!json_object_object_get_ex(obj, key, &j))
		return;
	s = json_object_get_string(j);
	if (s)
		snprintf(out, cap, "%s", s);
}

static void parse_message_obj(json_object *msg, struct tg_update *u)
{
	json_object *j = NULL;

	if (json_object_object_get_ex(msg, "message_id", &j))
		u->message_id = json_object_get_int64(j);
	if (json_object_object_get_ex(msg, "chat", &j)) {
		json_object *jid = NULL, *jtype = NULL;
		if (json_object_object_get_ex(j, "id", &jid))
			u->chat_id = json_object_get_int64(jid);
		if (json_object_object_get_ex(j, "type", &jtype)) {
			const char *t = json_object_get_string(jtype);
			if (t)
				snprintf(u->chat_type, sizeof(u->chat_type), "%s", t);
		}
	}
	if (json_object_object_get_ex(msg, "from", &j)) {
		json_object *jid = NULL;
		if (json_object_object_get_ex(j, "id", &jid))
			u->user_id = json_object_get_int64(jid);
	}
	if (json_object_object_get_ex(msg, "message_thread_id", &j)) {
		u->has_thread_id = true;
		u->thread_id = json_object_get_int64(j);
		if (u->thread_id == 1)
			u->has_thread_id = false;
	}
	if (json_object_object_get_ex(msg, "text", &j)) {
		const char *s = json_object_get_string(j);
		if (s)
			u->text = xstrdup(s);
	}
	if (json_object_object_get_ex(msg, "caption", &j)) {
		const char *s = json_object_get_string(j);
		if (s)
			u->caption = xstrdup(s);
	}
	if (json_object_object_get_ex(msg, "photo", &j) && json_object_is_type(j, json_type_array)) {
		size_t len = json_object_array_length(j);
		if (len > 0) {
			json_object *last = json_object_array_get_idx(j, len - 1);
			json_object *jf = NULL;
			u->has_photo = true;
			json_copy_str(last, "file_id", u->photo_file_id, sizeof(u->photo_file_id));
			json_copy_str(last, "file_unique_id", u->photo_unique_id, sizeof(u->photo_unique_id));
			if (json_object_object_get_ex(last, "file_size", &jf))
				u->photo_file_size = json_object_get_int64(jf);
		}
	}
	if (json_object_object_get_ex(msg, "document", &j)) {
		json_object *jf = NULL;
		u->has_document = true;
		json_copy_str(j, "file_id", u->doc_file_id, sizeof(u->doc_file_id));
		json_copy_str(j, "file_name", u->doc_file_name, sizeof(u->doc_file_name));
		if (json_object_object_get_ex(j, "file_size", &jf))
			u->doc_file_size = json_object_get_int64(jf);
	}
	if (json_object_object_get_ex(msg, "forum_topic_created", &j)) {
		u->is_topic_created = true;
		json_copy_str(j, "name", u->topic_created_name, sizeof(u->topic_created_name));
	}
	if (json_object_object_get_ex(msg, "forum_topic_closed", &j))
		u->is_topic_closed = true;
	if (json_object_object_get_ex(msg, "forum_topic_edited", &j)) {
		u->is_topic_edited = true;
		json_copy_str(j, "name", u->topic_edited_name, sizeof(u->topic_edited_name));
	}
}

int tg_get_updates(int64_t offset, int timeout_s, struct tg_update **out_updates,
		   size_t *out_count, struct tg_error *err)
{
	json_object *req = json_object_new_object();
	json_object *allowed = json_object_new_array();
	json_object *root, *jres;

	*out_updates = NULL;
	*out_count = 0;

	json_object_object_add(req, "offset", json_object_new_int64(offset));
	json_object_object_add(req, "timeout", json_object_new_int(timeout_s));
	json_object_array_add(allowed, json_object_new_string("message"));
	json_object_array_add(allowed, json_object_new_string("callback_query"));
	json_object_object_add(req, "allowed_updates", allowed);

	root = tg_post_json("getUpdates", req, timeout_s + 15, err);
	json_object_put(req);
	if (!root)
		return -1;

	if (!json_object_object_get_ex(root, "result", &jres) ||
	    !json_object_is_type(jres, json_type_array)) {
		json_object_put(root);
		return 0;
	}

	size_t n = json_object_array_length(jres);
	if (n == 0) {
		json_object_put(root);
		return 0;
	}

	struct tg_update *arr = calloc(n, sizeof(struct tg_update));
	if (!arr) {
		json_object_put(root);
		return -1;
	}

	for (size_t i = 0; i < n; i++) {
		json_object *item = json_object_array_get_idx(jres, i);
		json_object *j = NULL;
		struct tg_update *u = &arr[i];

		if (json_object_object_get_ex(item, "update_id", &j))
			u->update_id = json_object_get_int64(j);

		if (json_object_object_get_ex(item, "callback_query", &j)) {
			json_object *jc = NULL;
			u->is_callback = true;
			json_copy_str(j, "id", u->callback_id, sizeof(u->callback_id));
			json_copy_str(j, "data", u->callback_data, sizeof(u->callback_data));
			if (json_object_object_get_ex(j, "message", &jc))
				parse_message_obj(jc, u);
			/* Override user_id with callback sender */
			if (json_object_object_get_ex(j, "from", &jc)) {
				json_object *jid = NULL;
				if (json_object_object_get_ex(jc, "id", &jid))
					u->user_id = json_object_get_int64(jid);
			}
		} else if (json_object_object_get_ex(item, "message", &j)) {
			parse_message_obj(j, u);
		}
	}

	json_object_put(root);
	*out_updates = arr;
	*out_count = n;
	return 0;
}

bool tg_send_message_plain(int64_t chat_id, int64_t thread_id, int64_t reply_to_msg_id,
			   const char *text, const struct tg_entity *ents, size_t ent_count,
			   const struct tg_keyboard *kb, bool force_reply,
			   int64_t *out_msg_id, struct tg_error *err)
{
	json_object *req = json_object_new_object();
	json_object *root, *jres = NULL, *jmid = NULL;

	json_object_object_add(req, "chat_id", json_object_new_int64(chat_id));
	if (thread_id > 0)
		json_object_object_add(req, "message_thread_id", json_object_new_int64(thread_id));
	if (reply_to_msg_id > 0) {
		json_object *rp = json_object_new_object();
		json_object_object_add(rp, "message_id", json_object_new_int64(reply_to_msg_id));
		json_object_object_add(req, "reply_parameters", rp);
	}
	json_object_object_add(req, "text", json_object_new_string(text));
	if (ents && ent_count > 0)
		json_object_object_add(req, "entities", build_entities_json(ents, ent_count));
	add_no_link_preview(req);

	if (force_reply) {
		json_object *fr = json_object_new_object();
		json_object_object_add(fr, "force_reply", json_object_new_boolean(1));
		json_object_object_add(fr, "selective", json_object_new_boolean(1));
		json_object_object_add(req, "reply_markup", fr);
	} else if (kb && kb->num_rows > 0) {
		json_object_object_add(req, "reply_markup", build_keyboard_json(kb));
	}

	root = tg_post_json("sendMessage", req, 30, err);
	json_object_put(req);
	if (!root)
		return false;

	if (out_msg_id && json_object_object_get_ex(root, "result", &jres) &&
	    json_object_object_get_ex(jres, "message_id", &jmid)) {
		*out_msg_id = json_object_get_int64(jmid);
	}
	json_object_put(root);
	return true;
}

bool tg_edit_message_text_plain(int64_t chat_id, int64_t message_id, const char *text,
				const struct tg_entity *ents, size_t ent_count,
				const struct tg_keyboard *kb, struct tg_error *err)
{
	json_object *req = json_object_new_object();
	json_object *root;

	json_object_object_add(req, "chat_id", json_object_new_int64(chat_id));
	json_object_object_add(req, "message_id", json_object_new_int64(message_id));
	json_object_object_add(req, "text", json_object_new_string(text));
	if (ents && ent_count > 0)
		json_object_object_add(req, "entities", build_entities_json(ents, ent_count));
	add_no_link_preview(req);
	if (kb && kb->num_rows > 0)
		json_object_object_add(req, "reply_markup", build_keyboard_json(kb));

	root = tg_post_json("editMessageText", req, 30, err);
	json_object_put(req);
	if (!root)
		return false;
	json_object_put(root);
	return true;
}

bool tg_edit_message_reply_markup(int64_t chat_id, int64_t message_id,
				  const struct tg_keyboard *kb, struct tg_error *err)
{
	json_object *req = json_object_new_object();
	json_object *root;

	json_object_object_add(req, "chat_id", json_object_new_int64(chat_id));
	json_object_object_add(req, "message_id", json_object_new_int64(message_id));
	if (kb && kb->num_rows > 0)
		json_object_object_add(req, "reply_markup", build_keyboard_json(kb));
	else {
		json_object *empty = json_object_new_object();
		json_object_object_add(empty, "inline_keyboard", json_object_new_array());
		json_object_object_add(req, "reply_markup", empty);
	}

	root = tg_post_json("editMessageReplyMarkup", req, 30, err);
	json_object_put(req);
	if (!root)
		return false;
	json_object_put(root);
	return true;
}

static bool tg_post_multipart(const char *method, int64_t chat_id, int64_t thread_id,
			      int64_t message_id, const char *field_name, const char *filename,
			      const uint8_t *data, size_t len, const char *caption,
			      const struct tg_keyboard *kb, const char *media_type,
			      int64_t *out_msg_id, struct tg_error *err)
{
	CURL *curl;
	curl_mime *mime;
	curl_mimepart *part;
	char url[512];
	char buf[64];
	struct strbuf resp;
	CURLcode rc;
	json_object *root, *jok = NULL, *jres = NULL, *jmid = NULL;

	if (err)
		memset(err, 0, sizeof(*err));

	curl = curl_easy_init();
	if (!curl)
		return false;

	snprintf(url, sizeof(url), "https://api.telegram.org/bot%s/%s",
		 g_cfg.telegram_bot_token, method);

	mime = curl_mime_init(curl);

	part = curl_mime_addpart(mime);
	curl_mime_name(part, "chat_id");
	snprintf(buf, sizeof(buf), "%lld", (long long)chat_id);
	curl_mime_data(part, buf, CURL_ZERO_TERMINATED);

	if (thread_id > 0) {
		part = curl_mime_addpart(mime);
		curl_mime_name(part, "message_thread_id");
		snprintf(buf, sizeof(buf), "%lld", (long long)thread_id);
		curl_mime_data(part, buf, CURL_ZERO_TERMINATED);
	}
	if (message_id > 0) {
		part = curl_mime_addpart(mime);
		curl_mime_name(part, "message_id");
		snprintf(buf, sizeof(buf), "%lld", (long long)message_id);
		curl_mime_data(part, buf, CURL_ZERO_TERMINATED);
	}

	if (media_type) {
		/* editMessageMedia: attach://file + media JSON */
		json_object *mobj = json_object_new_object();
		json_object_object_add(mobj, "type", json_object_new_string(media_type));
		json_object_object_add(mobj, "media", json_object_new_string("attach://file"));
		if (caption && *caption)
			json_object_object_add(mobj, "caption", json_object_new_string(caption));

		part = curl_mime_addpart(mime);
		curl_mime_name(part, "media");
		curl_mime_data(part, json_object_to_json_string_ext(mobj, JSON_C_TO_STRING_PLAIN), CURL_ZERO_TERMINATED);
		json_object_put(mobj);

		part = curl_mime_addpart(mime);
		curl_mime_name(part, "file");
		curl_mime_filename(part, filename);
		curl_mime_data(part, (const char *)data, len);
	} else {
		part = curl_mime_addpart(mime);
		curl_mime_name(part, field_name);
		curl_mime_filename(part, filename);
		curl_mime_data(part, (const char *)data, len);

		if (caption && *caption) {
			part = curl_mime_addpart(mime);
			curl_mime_name(part, "caption");
			curl_mime_data(part, caption, CURL_ZERO_TERMINATED);
		}
	}

	if (kb && kb->num_rows > 0) {
		json_object *kobj = build_keyboard_json(kb);
		part = curl_mime_addpart(mime);
		curl_mime_name(part, "reply_markup");
		curl_mime_data(part, json_object_to_json_string_ext(kobj, JSON_C_TO_STRING_PLAIN), CURL_ZERO_TERMINATED);
		json_object_put(kobj);
	}

	sb_init(&resp);
	curl_easy_setopt(curl, CURLOPT_URL, url);
	curl_easy_setopt(curl, CURLOPT_MIMEPOST, mime);
	curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, curl_write_cb);
	curl_easy_setopt(curl, CURLOPT_WRITEDATA, &resp);
	curl_easy_setopt(curl, CURLOPT_TIMEOUT, 300L);
	curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT, 15L);

	rc = curl_easy_perform(curl);
	curl_mime_free(mime);
	curl_easy_cleanup(curl);

	if (rc != CURLE_OK) {
		if (err) {
			err->code = -1;
			snprintf(err->description, sizeof(err->description), "%s", curl_easy_strerror(rc));
		}
		sb_free(&resp);
		return false;
	}

	root = json_tokener_parse(resp.data ? resp.data : "");
	sb_free(&resp);
	if (!root)
		return false;

	if (!json_object_object_get_ex(root, "ok", &jok) || !json_object_get_boolean(jok)) {
		fill_tg_error(root, err);
		json_object_put(root);
		return false;
	}

	if (out_msg_id && json_object_object_get_ex(root, "result", &jres) &&
	    json_object_object_get_ex(jres, "message_id", &jmid)) {
		*out_msg_id = json_object_get_int64(jmid);
	}
	json_object_put(root);
	return true;
}

bool tg_send_photo_bytes(int64_t chat_id, int64_t thread_id, const uint8_t *png, size_t len,
			 const char *caption, const struct tg_keyboard *kb,
			 int64_t *out_msg_id, struct tg_error *err)
{
	return tg_post_multipart("sendPhoto", chat_id, thread_id, 0, "photo", "screenshot.png",
				 png, len, caption, kb, NULL, out_msg_id, err);
}

bool tg_send_document_bytes(int64_t chat_id, int64_t thread_id, const uint8_t *data, size_t len,
			    const char *filename, const char *caption, const struct tg_keyboard *kb,
			    int64_t *out_msg_id, struct tg_error *err)
{
	return tg_post_multipart("sendDocument", chat_id, thread_id, 0, "document", filename,
				 data, len, caption, kb, NULL, out_msg_id, err);
}

bool tg_edit_message_media_photo(int64_t chat_id, int64_t message_id, const uint8_t *png, size_t len,
				 const char *caption, const struct tg_keyboard *kb, struct tg_error *err)
{
	return tg_post_multipart("editMessageMedia", chat_id, 0, message_id, NULL, "screenshot.png",
				 png, len, caption, kb, "photo", NULL, err);
}

bool tg_edit_message_media_document(int64_t chat_id, int64_t message_id, const uint8_t *data, size_t len,
				    const char *filename, const char *caption, const struct tg_keyboard *kb,
				    struct tg_error *err)
{
	return tg_post_multipart("editMessageMedia", chat_id, 0, message_id, NULL, filename,
				 data, len, caption, kb, "document", NULL, err);
}

bool tg_edit_message_caption(int64_t chat_id, int64_t message_id, const char *caption,
			     const struct tg_keyboard *kb, struct tg_error *err)
{
	json_object *req = json_object_new_object();
	json_object *root;

	json_object_object_add(req, "chat_id", json_object_new_int64(chat_id));
	json_object_object_add(req, "message_id", json_object_new_int64(message_id));
	json_object_object_add(req, "caption", json_object_new_string(caption ? caption : ""));
	if (kb && kb->num_rows > 0)
		json_object_object_add(req, "reply_markup", build_keyboard_json(kb));

	root = tg_post_json("editMessageCaption", req, 30, err);
	json_object_put(req);
	if (!root)
		return false;
	json_object_put(root);
	return true;
}

bool tg_answer_callback(const char *callback_id, const char *text, bool show_alert)
{
	json_object *req = json_object_new_object();
	json_object *root;

	json_object_object_add(req, "callback_query_id", json_object_new_string(callback_id));
	if (text && *text)
		json_object_object_add(req, "text", json_object_new_string(text));
	if (show_alert)
		json_object_object_add(req, "show_alert", json_object_new_boolean(1));

	root = tg_post_json("answerCallbackQuery", req, 15, NULL);
	json_object_put(req);
	if (!root)
		return false;
	json_object_put(root);
	return true;
}

bool tg_send_chat_action(int64_t chat_id, int64_t thread_id, const char *action)
{
	json_object *req = json_object_new_object();
	json_object *root;

	json_object_object_add(req, "chat_id", json_object_new_int64(chat_id));
	if (thread_id > 0)
		json_object_object_add(req, "message_thread_id", json_object_new_int64(thread_id));
	json_object_object_add(req, "action", json_object_new_string(action));

	root = tg_post_json("sendChatAction", req, 10, NULL);
	json_object_put(req);
	if (!root)
		return false;
	json_object_put(root);
	return true;
}

static bool tg_topic_op(const char *method, int64_t chat_id, int64_t thread_id,
			const char *name, struct tg_error *err)
{
	json_object *req = json_object_new_object();
	json_object *root;

	json_object_object_add(req, "chat_id", json_object_new_int64(chat_id));
	json_object_object_add(req, "message_thread_id", json_object_new_int64(thread_id));
	if (name)
		json_object_object_add(req, "name", json_object_new_string(name));

	root = tg_post_json(method, req, 20, err);
	json_object_put(req);
	if (!root)
		return false;
	json_object_put(root);
	return true;
}

bool tg_edit_forum_topic(int64_t chat_id, int64_t thread_id, const char *name, struct tg_error *err)
{
	return tg_topic_op("editForumTopic", chat_id, thread_id, name, err);
}

bool tg_close_forum_topic(int64_t chat_id, int64_t thread_id, struct tg_error *err)
{
	return tg_topic_op("closeForumTopic", chat_id, thread_id, NULL, err);
}

bool tg_reopen_forum_topic(int64_t chat_id, int64_t thread_id, struct tg_error *err)
{
	return tg_topic_op("reopenForumTopic", chat_id, thread_id, NULL, err);
}

bool tg_delete_forum_topic(int64_t chat_id, int64_t thread_id, struct tg_error *err)
{
	return tg_topic_op("deleteForumTopic", chat_id, thread_id, NULL, err);
}

static size_t file_write_cb(void *ptr, size_t size, size_t nmemb, void *userdata)
{
	return fwrite(ptr, size, nmemb, (FILE *)userdata);
}

bool tg_download_file(const char *file_id, const char *dest_path, struct tg_error *err)
{
	json_object *req = json_object_new_object();
	json_object *root, *jres = NULL, *jpath = NULL;
	const char *fpath;
	char url[1024];
	CURL *curl;
	FILE *fp;
	CURLcode rc;

	json_object_object_add(req, "file_id", json_object_new_string(file_id));
	root = tg_post_json("getFile", req, 30, err);
	json_object_put(req);
	if (!root)
		return false;

	if (!json_object_object_get_ex(root, "result", &jres) ||
	    !json_object_object_get_ex(jres, "file_path", &jpath)) {
		json_object_put(root);
		return false;
	}
	fpath = json_object_get_string(jpath);
	if (!fpath || !*fpath) {
		json_object_put(root);
		return false;
	}
	snprintf(url, sizeof(url), "https://api.telegram.org/file/bot%s/%s",
		 g_cfg.telegram_bot_token, fpath);
	json_object_put(root);

	fp = fopen(dest_path, "wb");
	if (!fp)
		return false;

	curl = curl_easy_init();
	if (!curl) {
		fclose(fp);
		return false;
	}
	curl_easy_setopt(curl, CURLOPT_URL, url);
	curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, file_write_cb);
	curl_easy_setopt(curl, CURLOPT_WRITEDATA, fp);
	curl_easy_setopt(curl, CURLOPT_TIMEOUT, 300L);
	rc = curl_easy_perform(curl);
	curl_easy_cleanup(curl);
	fclose(fp);

	if (rc != CURLE_OK) {
		unlink(dest_path);
		return false;
	}
	return true;
}

/* ── High-level Entity Fallback & Splitting Helpers ────────────────── */

static int64_t send_part_with_fallback(int64_t chat_id, int64_t thread_id, int64_t reply_to_msg_id,
				       const char *markdown_part, const struct tg_keyboard *kb)
{
	char *plain = NULL;
	struct tg_entity *ents = NULL;
	size_t ent_count = 0;
	int64_t msg_id = 0;
	struct tg_error err;

	convert_to_entities(markdown_part, &plain, &ents, &ent_count);
	if (!plain || !*plain) {
		free(plain);
		free(ents);
		return 0;
	}

	for (int phase = 0; phase < 2; phase++) {
		const struct tg_entity *use_ents = (phase == 0 && ent_count > 0) ? ents : NULL;
		size_t use_cnt = (phase == 0) ? ent_count : 0;

		if (tg_send_message_plain(chat_id, thread_id, reply_to_msg_id, plain,
					  use_ents, use_cnt, kb, false, &msg_id, &err)) {
			free(plain);
			free(ents);
			return msg_id;
		}
		if (err.code == 429 && err.retry_after > 0) {
			if (err.retry_after > MAX_RETRY_AFTER_S)
				break;
			sleep((unsigned int)(err.retry_after + 1));
			if (tg_send_message_plain(chat_id, thread_id, reply_to_msg_id, plain,
						  use_ents, use_cnt, kb, false, &msg_id, &err)) {
				free(plain);
				free(ents);
				return msg_id;
			}
		}
		if (tg_is_thread_gone(&err))
			break;
	}

	free(plain);
	free(ents);
	return 0;
}

int64_t tg_safe_reply(int64_t chat_id, int64_t thread_id, int64_t reply_to_msg_id,
		      const char *markdown_text, const struct tg_keyboard *kb)
{
	char **parts = NULL;
	size_t count = 0;
	int64_t last_id = 0;

	if (split_message(markdown_text, CCGRAM_TG_SPLIT_LEN, &parts, &count) != 0)
		return 0;

	for (size_t i = 0; i < count; i++) {
		const struct tg_keyboard *use_kb = (i == 0) ? kb : NULL;
		int64_t use_reply = (i == 0) ? reply_to_msg_id : 0;
		if (i > 0)
			tg_rate_limit_send(chat_id);
		last_id = send_part_with_fallback(chat_id, thread_id, use_reply, parts[i], use_kb);
		if (last_id <= 0)
			break;
		topic_tail_record(chat_id, thread_id, last_id);
	}
	free_str_array(parts, count);
	return last_id;
}

int64_t tg_rate_limit_send_message(int64_t chat_id, int64_t thread_id, const char *markdown_text,
				   const struct tg_keyboard *kb, int split_max_len)
{
	char **parts = NULL;
	size_t count = 0;
	int64_t last_id = 0;

	tg_rate_limit_send(chat_id);
	if (split_message(markdown_text, (size_t)split_max_len, &parts, &count) != 0)
		return 0;

	for (size_t i = 0; i < count; i++) {
		const struct tg_keyboard *use_kb = (i == 0) ? kb : NULL;
		if (i > 0)
			tg_rate_limit_send(chat_id);
		last_id = send_part_with_fallback(chat_id, thread_id, 0, parts[i], use_kb);
		if (last_id <= 0)
			break;
	}
	free_str_array(parts, count);
	return last_id;
}

bool tg_safe_edit(int64_t chat_id, int64_t message_id, const char *markdown_text, const struct tg_keyboard *kb)
{
	struct tg_error err;
	return tg_edit_with_fallback(chat_id, message_id, markdown_text, kb, &err);
}

bool tg_edit_with_fallback(int64_t chat_id, int64_t message_id, const char *markdown_text,
			   const struct tg_keyboard *kb, struct tg_error *err)
{
	char *plain = NULL;
	struct tg_entity *ents = NULL;
	size_t ent_count = 0;
	bool ok = false;

	convert_to_entities(markdown_text, &plain, &ents, &ent_count);
	if (!plain)
		return false;

	ok = tg_edit_message_text_plain(chat_id, message_id, plain, ents, ent_count, kb, err);
	if (!ok && (!err || err->code != 429)) {
		ok = tg_edit_message_text_plain(chat_id, message_id, plain, NULL, 0, kb, err);
	}

	free(plain);
	free(ents);
	return ok;
}
