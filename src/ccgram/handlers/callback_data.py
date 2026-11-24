"""Callback data constants for Telegram inline keyboards.

Defines all CB_* prefixes used for routing callback queries in the bot.
Each prefix identifies a specific action or navigation target.
"""

# Screenshot
CB_SCREENSHOT_REFRESH = "ss:ref:"

# Screen diff buffer controls
CB_SCREEN_DIFF = "sd:"  # sd:buf / sd:ent / sd:esc / sd:right

# Sessions dashboard
CB_SESSIONS_REFRESH = "sess:ref"
CB_SESSIONS_KILL = "sess:kill:"  # sess:kill:<window_id>
CB_SESSIONS_KILL_CONFIRM = "sess:killok:"  # sess:killok:<window_id>
CB_SESSIONS_RENAME = "sess:rn:"  # sess:rn:<window_id>
CB_SESSIONS_SCREENSHOT = "sess:scr:"  # sess:scr:<window_id>

# Status message action buttons
CB_STATUS_SCREENSHOT = "st:ss:"  # st:ss:<window_id>

# Screenshot control keys
CB_KEYS_PREFIX = "kb:"  # kb:<key_id>:<window>

# Live view (auto-refreshing screenshot)
CB_LIVE_START = "lv:go:"  # lv:go:<target> (window_id or window_id:pane_id)
CB_LIVE_STOP = "lv:stop:"  # lv:stop:<target>
