DOMAIN = "ssc_student_client"
CONF_MODE = "mode"                 # "direct" (autonomous: Home Assistant talks to WebUntis itself) or "parent" (SSC Parent link)
MODE_DIRECT, MODE_PARENT = "direct", "parent"
CONF_URL = "url"                   # parent mode: the SSC Parent link
CONF_SERVER, CONF_SCHOOL, CONF_USER, CONF_KEY = "server", "school", "user", "key"   # direct mode: Untis Mobile login
CONF_SCAN_MINUTES = "scan_minutes"
DEFAULT_SCAN_MINUTES = 5           # parent mode (cheap, local)
DEFAULT_SCAN_DIRECT = 30           # direct mode (be gentle with the school's server)
