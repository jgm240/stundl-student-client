# Stundl Student Client (SSC) for Home Assistant

Two ways to get your school data into Home Assistant - pick one when you add the integration:

| | **Autonomous (direct)** | **From my Mac (Parent link)** |
|---|---|---|
| Needs the Mac on? | **No** - Home Assistant talks to WebUntis itself | Yes (Parent mode on, Mac awake) |
| You give it | the Untis Mobile QR text (a secret key - treat like a password) | the Parent link (read-only) |
| Fetch interval | 30 min (5-240) | 5 min (1-60) |
| Cached copy while offline | yes | yes |

## Install
1. **Add the integration files** (once):
   - **HACS** (recommended): HACS → ⋮ → *Custom repositories* → URL `https://github.com/jgm240/stundl-student-client`,
     category *Integration* → Add → search "Stundl Student Client" → Download.
   - **Manual**: download the repository ZIP, copy the folder `custom_components/ssc_student_client` into your Home
     Assistant `config/custom_components/`.
   Then **restart Home Assistant**.
2. **Settings → Devices & services → Add integration → Stundl Student Client (SSC)**, then choose a source:

### Autonomous (direct) - what you need
1. In WebUntis (in a browser): your name top right → **Profile → Freigaben / Access → "Untis Mobile"** → *show QR code*.
   (If your school turned this off you can't use direct mode - use the Parent link instead.)
2. Scan the QR code with your phone's camera app and **copy the text it shows** - it starts with `untis://setschool?…`.
   (Or read school, server, user and key out of that text.)
3. Paste the text into the first field and submit. Home Assistant checks the login first.
4. Keep Home Assistant's clock correct (NTP): the login uses a time-based code, and a clock that is off by more than
   about 30 seconds makes WebUntis refuse it. If the secret is ever revoked (you generate a new QR code), Home Assistant
   asks you to sign in again.

### From my Mac (Parent link)
On your Mac: SSC → Settings → Parent mode → **Turn on** → **Copy browser link**, and paste it. Home Assistant must be able
to reach the Mac on your home network.

## What you get
| Entity | |
|---|---|
| `sensor` Next lesson / Lessons today | with room, teacher, today's list |
| `sensor` Homework open / missed / due soon | statuses come from SSC (done / due soon / pending / missed) |
| `sensor` Next exam, Study plan today, Last sync on the Mac | |
| `binary_sensor` Missed homework, Lesson cancelled today, Showing cached data | for automations |
| `calendar` Timetable | lessons, exams, homework due dates, your study plan |

## Dashboard ideas
```yaml
type: vertical-stack
cards:
  - type: entities
    title: School
    entities:
      - sensor.stundl_student_client_next_lesson
      - sensor.stundl_student_client_homework_open
      - binary_sensor.stundl_student_client_missed_homework
      - sensor.stundl_student_client_next_exam
  - type: calendar
    entities: [calendar.stundl_student_client_timetable]
  - type: iframe          # the ready-made wall display page (needs http HA, or an https Parent link)
    url: http://192.168.1.20:8766/p/<code>/wall
    aspect_ratio: 60%
```
Automation example - notify when something is cancelled or missed: trigger on the two `binary_sensor`s turning `on`.

## Good to know
- Home Assistant stores the secret (Untis Mobile key or Parent link) in its own configuration, like any integration
  setting. Treat it as a secret. Revoke the Untis Mobile key by generating a new QR code in WebUntis; rotate the Parent
  link in SSC (Parent mode → *New link*).
- Direct mode uses WebUntis's internal mobile endpoints. It was only tested against a simulated server, so field names
  may need small adjustments on your school's real server - report anything odd (the "notes" attribute lists failed
  parts). The integration also hasn't been run inside Home Assistant yet.
- Parent-link mode is only as fresh as the Mac's last sync - keep SSC's daily sync (or the app) running.
- Unofficial; not affiliated with Untis GmbH or Home Assistant. This integration could only be checked for syntax
  and logic outside Home Assistant - please report anything that misbehaves on a real installation.
