#include "ccgram.h"

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

volatile int g_running = 1;

static void test_arg_parser(void)
{
	size_t count = 0;
	const struct cmd_def *cmds = handlers_get_commands(&count);
	const struct cmd_def *out = NULL;
	const char *cands[8];
	size_t ncand = 0;

	/* Exact match */
	assert(match_command_token("screenshot", cmds, count, &out, cands, 8, &ncand) == CMD_MATCH_OK);
	assert(strcmp(out->name, "screenshot") == 0);

	/* Alias match */
	assert(match_command_token("help", cmds, count, &out, cands, 8, &ncand) == CMD_MATCH_OK);
	assert(strcmp(out->name, "commands") == 0);

	/* Unambiguous prefix */
	assert(match_command_token("sc", cmds, count, &out, cands, 8, &ncand) == CMD_MATCH_OK);
	assert(strcmp(out->name, "screenshot") == 0);

	assert(match_command_token("ses", cmds, count, &out, cands, 8, &ncand) == CMD_MATCH_OK);
	assert(strcmp(out->name, "sessions") == 0);

	assert(match_command_token("unb", cmds, count, &out, cands, 8, &ncand) == CMD_MATCH_OK);
	assert(strcmp(out->name, "unbind") == 0);

	/* Ambiguous prefix: "bind" matches exact "bind" (not ambiguous!) */
	assert(match_command_token("bind", cmds, count, &out, cands, 8, &ncand) == CMD_MATCH_OK);
	assert(strcmp(out->name, "bind") == 0);

	/* Ambiguous prefix: "e" matches enter, esc */
	assert(match_command_token("e", cmds, count, &out, cands, 8, &ncand) == CMD_MATCH_AMBIGUOUS);
	assert(ncand == 2);

	/* Unknown */
	assert(match_command_token("nonexistent", cmds, count, &out, cands, 8, &ncand) == CMD_MATCH_NONE);
}

static void test_router_and_tail(void)
{
	char wid[192], disp[128];

	router_init();
	topic_tail_reset();

	router_bind_thread(100, 42, "cc_dev:@0", "dev");
	assert(router_get_window_for_thread(100, 42, wid, sizeof(wid)));
	assert(strcmp(wid, "cc_dev:@0") == 0);
	router_get_display_name("cc_dev:@0", disp, sizeof(disp));
	assert(strcmp(disp, "dev") == 0);
	assert(router_user_owns_window(100, "cc_dev:@0"));

	/* 1:1 eviction: binding thread 99 to same window evicts thread 42 */
	router_bind_thread(100, 99, "cc_dev:@0", "dev");
	assert(!router_get_window_for_thread(100, 42, wid, sizeof(wid)));
	assert(router_get_window_for_thread(100, 99, wid, sizeof(wid)));

	router_set_group_chat_id(100, 99, -100123456);
	assert(router_resolve_chat_id(100, 99) == -100123456);
	assert(router_get_window_for_chat_thread(-100123456, 99, wid, sizeof(wid)));
	assert(strcmp(wid, "cc_dev:@0") == 0);

	assert(router_unbind_thread(100, 99, wid, sizeof(wid)));
	assert(strcmp(wid, "cc_dev:@0") == 0);
	assert(!router_user_owns_window(100, "cc_dev:@0"));

	/* Topic tail */
	assert(topic_tail_is_last(-100, 42, 10));
	topic_tail_record(-100, 42, 50);
	assert(!topic_tail_is_last(-100, 42, 49));
	assert(topic_tail_is_last(-100, 42, 50));
	topic_tail_clear(-100, 42);
	assert(topic_tail_is_last(-100, 42, 1));
}

static void test_sidecar_extract(void)
{
	const char *sample = "noise\n__CCG_DONE_1234__:BEGIN\nhello\nworld\n__CCG_DONE_1234__:0\n";
	char *out = NULL;
	bool completed = false;

	assert(extract_sidecar_output(sample, "__CCG_DONE_1234__", &out, &completed));
	assert(completed == true);
	assert(strcmp(out, "hello\nworld") == 0);
	free(out);

	const char *running = "__CCG_DONE_abcd__:BEGIN\nline1\n";
	assert(extract_sidecar_output(running, "__CCG_DONE_abcd__", &out, &completed));
	assert(completed == false);
	assert(strcmp(out, "line1") == 0);
	free(out);

	assert(!extract_sidecar_output("no marker", "__CCG_DONE_abcd__", &out, &completed));
}

static void test_entities_and_split(void)
{
	char *plain = NULL;
	struct tg_entity *ents = NULL;
	size_t cnt = 0;

	convert_to_entities("Hello *bold* `code`\n```c\nint x = 1;\n```", &plain, &ents, &cnt);
	assert(plain != NULL);
	assert(cnt == 3);
	assert(strcmp(ents[0].type, "bold") == 0);
	assert(strcmp(ents[1].type, "code") == 0);
	assert(strcmp(ents[2].type, "pre") == 0);
	assert(strcmp(ents[2].language, "c") == 0);
	free(plain);
	free(ents);

	/* UTF-16 splitting */
	char **parts = NULL;
	size_t nparts = 0;
	assert(split_message("line1\nline2\nline3", 12, &parts, &nparts) == 0);
	assert(nparts == 2);
	assert(strcmp(parts[0], "line1\nline2") == 0);
	assert(strcmp(parts[1], "line3") == 0);
	free_str_array(parts, nparts);
}

static void test_diff_and_live_tail(void)
{
	char *t = diff_tail_lines("\x1b[31mhello\x1b[0m\nworld\n", 100);
	assert(strcmp(t, "hello\nworld") == 0);
	free(t);

	char *lt = live_tail_lines_ansi("\x1b[32mline1\x1b[0m\nline2\nline3", 12);
	assert(strcmp(lt, "line2\nline3") == 0);
	free(lt);
}

static void test_screenshot_png(void)
{
	size_t len = 0;
	uint8_t *png = screenshot_text_to_png("\x1b[32mOK\x1b[0m \xe2\x94\x80 test", 15, true, false, &len);
	assert(png != NULL);
	assert(len > 32);
	/* PNG signature */
	assert(png[0] == 137 && png[1] == 'P' && png[2] == 'N' && png[3] == 'G');
	free(png);

	/* Tab expansion must not overflow cell buffer */
	uint8_t *tpng = screenshot_text_to_png("\t\t\t", 15, true, false, &len);
	assert(tpng != NULL);
	assert(len > 32);
	free(tpng);
}

static void test_sanitize(void)
{
	char buf[256];

	sanitize_filename("../../etc/passwd", buf, sizeof(buf));
	assert(strcmp(buf, "passwd") == 0);

	sanitize_filename("...", buf, sizeof(buf));
	assert(strcmp(buf, "unnamed") == 0);

	sanitize_caption("hello\nworld\x01!", buf, sizeof(buf));
	assert(strcmp(buf, "hello world!") == 0);
}

static void test_config(void)
{
	setenv("TELEGRAM_BOT_TOKEN", "12345:TESTTOKEN", 1);
	setenv("ALLOWED_USERS", "111, 222", 1);
	assert(config_init() == 0);
	assert(config_is_user_allowed(111));
	assert(config_is_user_allowed(222));
	assert(!config_is_user_allowed(333));
}

int main(void)
{
	test_config();
	test_arg_parser();
	test_router_and_tail();
	test_sidecar_extract();
	test_entities_and_split();
	test_diff_and_live_tail();
	test_screenshot_png();
	test_sanitize();
	printf("All C unit tests passed!\n");
	return 0;
}
