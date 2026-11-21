# Stundl Student Client (SSC) for Home Assistant

Runs inside Home Assistant and fetches your school data **by itself** from SSC's read-only Parent mode - no credentials
involved, only the secret Parent link. It keeps a cached copy, so the sensors keep working (and show "cached") while
the Mac is off or asleep.

## Install
1. On your Mac: SSC → Settings → **Parent mode → Turn on**, then **Copy browser link**.
2. Install the integration: either add this repository to **HACS** as a custom repository (Integration), or copy the folder
   `custom_components/ssc_student_client` from this repository into your Home Assistant `config/custom_components/`.
   Then restart Home Assistant.
3. Settings → Devices & services → **Add integration** → *Stundl Student Client (SSC)* → paste the link.

The link must be reachable from Home Assistant (same home network). Fetch interval: Configure → 5 minutes by default.

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
- Home Assistant stores the Parent link in its own configuration (like any integration setting). Treat it as a secret;
  rotate it any time in SSC (Parent mode → *New link*) and update the integration.
- The data is only as fresh as the Mac's last sync - keep SSC's daily sync (or the app) running.
- Unofficial; not affiliated with Untis GmbH or Home Assistant. This integration could only be checked for syntax
  and logic outside Home Assistant - please report anything that misbehaves on a real installation.
