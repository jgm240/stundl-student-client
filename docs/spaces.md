# Spaces: separate parts of one SSC Server

A **space** is a sealed part of the server - a class, a family, a school. Each space has its own

- **administrators** (they manage that space only),
- **students** (each with their own profile: data, WebUntis key, homework ticks, plan),
- **parent / guest accounts** (they see the students they were given),
- **tokens**: one WebUntis key and one Parent link per student, an optional mail address list per student.

Nobody inside a space can read or change anything of another space, and none of them can reach the server settings, the machine,
the update, the health page or the Admin Panel. Accounts and children that are in no space keep working exactly as before.

## Who can do what

| | server administrator | space administrator | student | parent / guest in a space |
|---|---|---|---|---|
| Admin Panel (network, power, updates, mail server ...) | yes | no | no | no |
| Create / remove spaces | yes | no | no | no |
| Create students, parents, other space administrators | yes (any space) | their own space | no | no |
| WebUntis key, Parent link, sync, mail addresses | any | every student of their space | their own | no |
| The full app (timetable, homework, plan ...) | all profiles | every student of their space | their own profile | the Parent view of their students |
| Change their own password | yes | yes | yes | yes |

## Set it up

In the Admin Panel open **Spaces**: create a space, then add its first administrator (and, if you like, students). From then on the
space administrator works on their own page, **/space** (link "My space" in the app), and adds the students and their parents.

On the console:

```
ssconfig space add class-3a "Class 3A"
ssconfig space admin class-3a teacher        # asks for a password
ssconfig space student class-3a ana "Ana S." # a student with a login and a profile of their own
ssconfig space list
ssconfig account space someone class-3a      # move an existing account into a space ('none' = out)
ssconfig profile add kid9 "Kid Nine" --space class-3a
```

A space can only be removed when it is empty. Deleting a student's login keeps their data unless you say otherwise.

## What is enforced (and tested)

Every request is answered for one profile, and the list of profiles an account may use comes from one function that looks at the account's
space and role - not from anything the browser sends. A cookie, parameter or token for another space's profile is ignored or refused.
Students and space administrators may use only a short allow-list of the app's routes (everything else is refused by default).
The tests in `tests/test_spaces.py` try the usual attacks: other spaces' profile ids in cookies and bodies, other spaces' accounts by name,
the admin routes, the Parent-link tokens, the shared sync log, e-mail recipients.

## Honest limits

- The **server administrator can see everything** - they run the machine, and anyone with root on it can read the files. Spaces separate
  the people *inside* the server from each other, not from its owner.
- Login names are unique across the whole server: a space administrator who picks a name that is taken learns that the name exists.
- **Mail** goes out through the one mail account the server administrator set up. A student's mail addresses receive only that student's
  changes; a space never receives the server's own list. A space can make the server send to at most 5 addresses per student.
- A student or space administrator can only point a WebUntis key at `*.webuntis.com` (otherwise they could make the server contact any host).
  Another address must be entered by the server administrator.
- The Parent link of a student shows that student only; anyone who has the link sees their timetable, homework and exams until you make a new link.
- All students share the machine: one student's sync uses the same CPU and network as everyone's.
- The pages for spaces are in English only.
