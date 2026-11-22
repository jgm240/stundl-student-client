/* SSC shared front-end runtime: language, theme, palette and accent colour - per user.
   Injected inline into every page by core.load_page() (so it also works in offline copies and static demos).
   English is the source language: pages are written in English and translated by exact text (see D below).
   Texts with a changing part use {placeholders}, e.g. "due in {n} days ({d})". */
(function (root) {
"use strict";

var LANGS = { en: "English", de: "Deutsch", fr: "Français", es: "Español", it: "Italiano", pt: "Português", nl: "Nederlands", pl: "Polski" };
var ORDER = ["de", "fr", "es", "it", "pt", "nl", "pl"];                 // column order of the rows in D (English is the key)
var LOCALES = { en: "en-GB", de: "de-DE", fr: "fr-FR", es: "es-ES", it: "it-IT", pt: "pt-PT", nl: "nl-NL", pl: "pl-PL" };

var D = [
// ---- navigation, header
["Today", "Heute", "Aujourd'hui", "Hoy", "Oggi", "Hoje", "Vandaag", "Dzisiaj"],
["Timetable", "Stundenplan", "Emploi du temps", "Horario", "Orario", "Horário", "Lesrooster", "Plan lekcji"],
["Homework", "Hausaufgaben", "Devoirs", "Deberes", "Compiti", "Trabalhos de casa", "Huiswerk", "Zadania domowe"],
["Exams", "Prüfungen", "Examens", "Exámenes", "Verifiche", "Exames", "Toetsen", "Sprawdziany"],
["Grades & absences", "Noten & Fehlzeiten", "Notes et absences", "Notas y ausencias", "Voti e assenze", "Notas e faltas", "Cijfers en absenties", "Oceny i nieobecności"],
["Grades", "Noten", "Notes", "Notas", "Voti", "Notas", "Cijfers", "Oceny"],
["Changes", "Änderungen", "Changements", "Cambios", "Modifiche", "Alterações", "Wijzigingen", "Zmiany"],
["Study plan", "Lernplan", "Plan d'étude", "Plan de estudio", "Piano di studio", "Plano de estudo", "Studieplanning", "Plan nauki"],
["Teachers & rooms", "Lehrer & Räume", "Enseignants et salles", "Profesores y aulas", "Docenti e aule", "Professores e salas", "Docenten en lokalen", "Nauczyciele i sale"],
["Teachers", "Lehrer", "Enseignants", "Profesores", "Docenti", "Professores", "Docenten", "Nauczyciele"],
["Messages", "Nachrichten", "Messages", "Mensajes", "Messaggi", "Mensagens", "Berichten", "Wiadomości"],
["Search", "Suche", "Recherche", "Buscar", "Cerca", "Pesquisa", "Zoeken", "Szukaj"],
["Settings", "Einstellungen", "Paramètres", "Ajustes", "Impostazioni", "Definições", "Instellingen", "Ustawienia"],
["Overview", "Übersicht", "Aperçu", "Resumen", "Panoramica", "Visão geral", "Overzicht", "Przegląd"],
["Insights", "Auswertungen", "Analyses", "Análisis", "Analisi", "Análises", "Inzichten", "Analizy"],
["Tools", "Werkzeuge", "Outils", "Herramientas", "Strumenti", "Ferramentas", "Hulpmiddelen", "Narzędzia"],
["Export…", "Exportieren…", "Exporter…", "Exportar…", "Esporta…", "Exportar…", "Exporteren…", "Eksportuj…"],
["Calendar (.ics)", "Kalender (.ics)", "Calendrier (.ics)", "Calendario (.ics)", "Calendario (.ics)", "Calendário (.ics)", "Agenda (.ics)", "Kalendarz (.ics)"],
["Homework CSV", "Hausaufgaben (CSV)", "Devoirs (CSV)", "Deberes (CSV)", "Compiti (CSV)", "Trabalhos (CSV)", "Huiswerk (CSV)", "Zadania (CSV)"],
["Exams CSV", "Prüfungen (CSV)", "Examens (CSV)", "Exámenes (CSV)", "Verifiche (CSV)", "Exames (CSV)", "Toetsen (CSV)", "Sprawdziany (CSV)"],
["Grades CSV", "Noten (CSV)", "Notes (CSV)", "Notas (CSV)", "Voti (CSV)", "Notas (CSV)", "Cijfers (CSV)", "Oceny (CSV)"],
["Absences CSV", "Fehlzeiten (CSV)", "Absences (CSV)", "Ausencias (CSV)", "Assenze (CSV)", "Faltas (CSV)", "Absenties (CSV)", "Nieobecności (CSV)"],
["Sync now", "Jetzt synchronisieren", "Synchroniser", "Sincronizar ahora", "Sincronizza ora", "Sincronizar agora", "Nu synchroniseren", "Synchronizuj teraz"],
["Syncing…", "Synchronisiere…", "Synchronisation…", "Sincronizando…", "Sincronizzazione…", "A sincronizar…", "Synchroniseren…", "Synchronizowanie…"],
["Loading…", "Lädt…", "Chargement…", "Cargando…", "Caricamento…", "A carregar…", "Laden…", "Ładowanie…"],
["No data yet. Press “Sync now”.", "Noch keine Daten. „Jetzt synchronisieren“ drücken.", "Pas encore de données. Appuyez sur « Synchroniser ».", "Aún no hay datos. Pulsa «Sincronizar ahora».", "Ancora nessun dato. Premi «Sincronizza ora».", "Ainda sem dados. Prima «Sincronizar agora».", "Nog geen gegevens. Druk op “Nu synchroniseren”.", "Brak danych. Naciśnij „Synchronizuj teraz”."],
["Synced {d}", "Synchronisiert {d}", "Synchronisé {d}", "Sincronizado {d}", "Sincronizzato {d}", "Sincronizado {d}", "Gesynchroniseerd {d}", "Zsynchronizowano {d}"],
["Not synced yet", "Noch nicht synchronisiert", "Pas encore synchronisé", "Aún sin sincronizar", "Non ancora sincronizzato", "Ainda não sincronizado", "Nog niet gesynchroniseerd", "Jeszcze nie zsynchronizowano"],
["never", "nie", "jamais", "nunca", "mai", "nunca", "nooit", "nigdy"],
["Close", "Schließen", "Fermer", "Cerrar", "Chiudi", "Fechar", "Sluiten", "Zamknij"],
// ---- today
["Good night", "Gute Nacht", "Bonne nuit", "Buenas noches", "Buonanotte", "Boa noite", "Goedenacht", "Dobranoc"],
["Good morning", "Guten Morgen", "Bonjour", "Buenos días", "Buongiorno", "Bom dia", "Goedemorgen", "Dzień dobry"],
["Good afternoon", "Guten Tag", "Bon après-midi", "Buenas tardes", "Buon pomeriggio", "Boa tarde", "Goedemiddag", "Dzień dobry"],
["Good evening", "Guten Abend", "Bonsoir", "Buenas noches", "Buonasera", "Boa noite", "Goedenavond", "Dobry wieczór"],
["{n} homework item(s) need attention", "{n} Hausaufgabe(n) brauchen Aufmerksamkeit", "{n} devoir(s) demandent votre attention", "{n} deber(es) requieren atención", "{n} compito/i richiedono attenzione", "{n} trabalho(s) de casa precisam de atenção", "{n} huiswerkitem(s) hebben aandacht nodig", "{n} zadań domowych wymaga uwagi"],
["Nothing urgent", "Nichts Dringendes", "Rien d'urgent", "Nada urgente", "Niente di urgente", "Nada urgente", "Niets dringends", "Nic pilnego"],
["NOW", "JETZT", "MAINTENANT", "AHORA", "ORA", "AGORA", "NU", "TERAZ"],
["NEXT", "ALS NÄCHSTES", "ENSUITE", "SIGUIENTE", "PROSSIMA", "A SEGUIR", "VOLGENDE", "NASTĘPNA"],
["No more lessons", "Keine weiteren Stunden", "Plus de cours", "No hay más clases", "Nessun'altra lezione", "Sem mais aulas", "Geen lessen meer", "Brak kolejnych lekcji"],
["Enjoy your day.", "Schönen Tag noch!", "Bonne journée !", "¡Que tengas un buen día!", "Buona giornata!", "Tenha um bom dia!", "Fijne dag!", "Miłego dnia!"],
["Next exam", "Nächste Prüfung", "Prochain examen", "Próximo examen", "Prossima verifica", "Próximo exame", "Volgende toets", "Następny sprawdzian"],
["none planned", "keine geplant", "aucun prévu", "ninguno previsto", "nessuna prevista", "nenhum previsto", "geen gepland", "brak zaplanowanych"],
["Study plan today", "Lernplan heute", "Plan d'étude du jour", "Plan de estudio de hoy", "Piano di studio di oggi", "Plano de estudo de hoje", "Studieplanning vandaag", "Plan nauki na dziś"],
["nothing planned", "nichts geplant", "rien de prévu", "nada previsto", "niente in programma", "nada planeado", "niets gepland", "nic nie zaplanowano"],
["{n} done", "{n} erledigt", "{n} fait(s)", "{n} hecho(s)", "{n} fatti", "{n} concluído(s)", "{n} klaar", "{n} gotowe"],
["unseen", "ungesehen", "non vu", "sin ver", "non visti", "por ver", "ongezien", "nieprzeczytane"],
["all seen", "alles gesehen", "tout vu", "todo visto", "tutto visto", "tudo visto", "alles gezien", "wszystko przeczytane"],
["{a} missed · {b} due soon", "{a} verpasst · {b} bald fällig", "{a} manqués · {b} bientôt dus", "{a} perdidos · {b} pronto", "{a} mancati · {b} in scadenza", "{a} em falta · {b} a vencer", "{a} gemist · {b} binnenkort", "{a} zaległe · {b} wkrótce"],
["Tomorrow · {d}", "Morgen · {d}", "Demain · {d}", "Mañana · {d}", "Domani · {d}", "Amanhã · {d}", "Morgen · {d}", "Jutro · {d}"],
["New changes", "Neue Änderungen", "Nouveaux changements", "Cambios nuevos", "Nuove modifiche", "Novas alterações", "Nieuwe wijzigingen", "Nowe zmiany"],
["Mark all seen", "Alle als gesehen markieren", "Tout marquer comme vu", "Marcar todo como visto", "Segna tutto come visto", "Marcar tudo como visto", "Alles als gezien markeren", "Oznacz wszystko jako przeczytane"],
["Nothing open.", "Nichts offen.", "Rien d'ouvert.", "Nada pendiente.", "Niente di aperto.", "Nada em aberto.", "Niets openstaand.", "Nic otwartego."],
["No lessons.", "Kein Unterricht.", "Pas de cours.", "Sin clases.", "Nessuna lezione.", "Sem aulas.", "Geen lessen.", "Brak lekcji."],
["Absence warnings", "Fehlzeiten-Warnungen", "Alertes d'absence", "Avisos de ausencias", "Avvisi di assenza", "Avisos de faltas", "Absentiewaarschuwingen", "Ostrzeżenia o nieobecnościach"],
["Study plan for today", "Lernplan für heute", "Plan d'étude du jour", "Plan de estudio de hoy", "Piano di studio di oggi", "Plano de estudo de hoje", "Studieplanning voor vandaag", "Plan nauki na dziś"],
// ---- timetable, lessons
["Week of {d}", "Woche ab {d}", "Semaine du {d}", "Semana del {d}", "Settimana del {d}", "Semana de {d}", "Week van {d}", "Tydzień od {d}"],
["All subjects", "Alle Fächer", "Toutes les matières", "Todas las asignaturas", "Tutte le materie", "Todas as disciplinas", "Alle vakken", "Wszystkie przedmioty"],
["All teachers", "Alle Lehrer", "Tous les enseignants", "Todos los profesores", "Tutti i docenti", "Todos os professores", "Alle docenten", "Wszyscy nauczyciele"],
["All rooms", "Alle Räume", "Toutes les salles", "Todas las aulas", "Tutte le aule", "Todas as salas", "Alle lokalen", "Wszystkie sale"],
["· today", "· heute", "· aujourd'hui", "· hoy", "· oggi", "· hoje", "· vandaag", "· dziś"],
["Lesson", "Unterricht", "Cours", "Clase", "Lezione", "Aula", "Les", "Lekcja"],
["cancelled", "entfällt", "annulé", "cancelada", "annullata", "cancelada", "vervallen", "odwołana"],
["CANCELLED", "ENTFÄLLT", "ANNULÉ", "CANCELADA", "ANNULLATA", "CANCELADA", "VERVALLEN", "ODWOŁANA"],
["changed", "geändert", "modifié", "modificada", "modificata", "alterada", "gewijzigd", "zmieniona"],
["Cancelled", "Entfällt", "Annulé", "Cancelada", "Annullata", "Cancelada", "Vervallen", "Odwołana"],
["Changed", "Geändert", "Modifié", "Modificada", "Modificata", "Alterada", "Gewijzigd", "Zmieniona"],
["Regular", "Regulär", "Normal", "Normal", "Regolare", "Normal", "Regulier", "Zwykła"],
["Time", "Zeit", "Heure", "Hora", "Ora", "Hora", "Tijd", "Godzina"],
["Teacher", "Lehrkraft", "Enseignant", "Profesor", "Docente", "Professor", "Docent", "Nauczyciel"],
["Room", "Raum", "Salle", "Aula", "Aula", "Sala", "Lokaal", "Sala"],
["Class / group", "Klasse / Gruppe", "Classe / groupe", "Clase / grupo", "Classe / gruppo", "Turma / grupo", "Klas / groep", "Klasa / grupa"],
["Class", "Klasse", "Classe", "Clase", "Classe", "Turma", "Klas", "Klasa"],
["Topic", "Thema", "Sujet", "Tema", "Argomento", "Tema", "Onderwerp", "Temat"],
["Type", "Art", "Type", "Tipo", "Tipo", "Tipo", "Type", "Typ"],
["What changed", "Was sich geändert hat", "Ce qui a changé", "Qué ha cambiado", "Cosa è cambiato", "O que mudou", "Wat er veranderd is", "Co się zmieniło"],
["Lesson content", "Unterrichtsinhalt", "Contenu du cours", "Contenido de la clase", "Contenuto della lezione", "Conteúdo da aula", "Lesinhoud", "Treść lekcji"],
["Lesson topic", "Thema der Stunde", "Sujet du cours", "Tema de la clase", "Argomento della lezione", "Tema da aula", "Onderwerp van de les", "Temat lekcji"],
["Notes", "Notizen", "Notes", "Notas", "Note", "Notas", "Notities", "Notatki"],
["Substitution", "Vertretung", "Remplacement", "Sustitución", "Supplenza", "Substituição", "Vervanging", "Zastępstwo"],
["Nothing entered for this lesson yet. WebUntis only shows content when the teacher has filled it in.", "Für diese Stunde wurde noch nichts eingetragen. WebUntis zeigt Inhalte nur, wenn die Lehrkraft sie ausgefüllt hat.", "Rien n'a encore été saisi pour ce cours. WebUntis n'affiche le contenu que si l'enseignant l'a renseigné.", "Aún no se ha introducido nada para esta clase. WebUntis solo muestra contenido cuando el profesor lo ha rellenado.", "Per questa lezione non è stato ancora inserito nulla. WebUntis mostra i contenuti solo se il docente li ha compilati.", "Ainda não foi introduzido nada para esta aula. O WebUntis só mostra conteúdo quando o professor o preenche.", "Voor deze les is nog niets ingevuld. WebUntis toont inhoud alleen als de docent die heeft ingevuld.", "Dla tej lekcji nic jeszcze nie wpisano. WebUntis pokazuje treść tylko wtedy, gdy nauczyciel ją uzupełni."],
["Related · {s}", "Dazu · {s}", "Lié · {s}", "Relacionado · {s}", "Correlati · {s}", "Relacionado · {s}", "Gerelateerd · {s}", "Powiązane · {s}"],
["Next {s} lessons", "Nächste Stunden {s}", "Prochains cours de {s}", "Próximas clases de {s}", "Prossime lezioni di {s}", "Próximas aulas de {s}", "Volgende lessen {s}", "Następne lekcje: {s}"],
["+ Add to study plan", "+ Zum Lernplan hinzufügen", "+ Ajouter au plan d'étude", "+ Añadir al plan de estudio", "+ Aggiungi al piano di studio", "+ Adicionar ao plano de estudo", "+ Toevoegen aan studieplanning", "+ Dodaj do planu nauki"],
["exam", "Prüfung", "examen", "examen", "verifica", "exame", "toets", "sprawdzian"],
["homework", "Hausaufgaben", "devoirs", "deberes", "compiti", "trabalhos", "huiswerk", "zadania"],
["timetable", "Stundenplan", "emploi du temps", "horario", "orario", "horário", "lesrooster", "plan lekcji"],
["grade", "Note", "note", "nota", "voto", "nota", "cijfer", "ocena"],
// ---- homework
["Missed", "Verpasst", "Manqué", "Perdido", "Mancato", "Em falta", "Gemist", "Zaległe"],
["Due soon", "Bald fällig", "Bientôt dû", "Pronto", "In scadenza", "A vencer", "Binnenkort", "Wkrótce"],
["Pending", "Offen", "En attente", "Pendiente", "In sospeso", "Pendente", "Openstaand", "Oczekujące"],
["Done", "Erledigt", "Fait", "Hecho", "Fatto", "Concluído", "Klaar", "Gotowe"],
["To do", "Zu erledigen", "À faire", "Por hacer", "Da fare", "Por fazer", "Te doen", "Do zrobienia"],
["Dismissed", "Ausgeblendet", "Masqué", "Descartado", "Nascosti", "Dispensados", "Verborgen", "Ukryte"],
["All", "Alle", "Tous", "Todos", "Tutti", "Todos", "Alle", "Wszystkie"],
["Nothing here.", "Hier ist nichts.", "Rien ici.", "Nada aquí.", "Niente qui.", "Nada aqui.", "Hier is niets.", "Nic tu nie ma."],
["Done and long-missed homework is dismissed automatically (change this in Settings).", "Erledigte und lange versäumte Hausaufgaben werden automatisch ausgeblendet (in den Einstellungen änderbar).", "Les devoirs faits ou manqués depuis longtemps sont masqués automatiquement (modifiable dans les paramètres).", "Los deberes hechos o perdidos hace mucho se descartan automáticamente (se cambia en Ajustes).", "I compiti fatti o scaduti da tempo vengono nascosti automaticamente (modificabile nelle impostazioni).", "Os trabalhos concluídos ou em falta há muito tempo são dispensados automaticamente (altere nas Definições).", "Voltooid en lang gemist huiswerk wordt automatisch verborgen (aanpasbaar in Instellingen).", "Wykonane i dawno zaległe zadania są ukrywane automatycznie (można to zmienić w Ustawieniach)."],
["due today", "heute fällig", "pour aujourd'hui", "para hoy", "per oggi", "para hoje", "vandaag", "na dziś"],
["due tomorrow", "morgen fällig", "pour demain", "para mañana", "per domani", "para amanhã", "morgen", "na jutro"],
["due in {n} days ({d})", "fällig in {n} Tagen ({d})", "dans {n} jours ({d})", "en {n} días ({d})", "tra {n} giorni ({d})", "em {n} dias ({d})", "over {n} dagen ({d})", "za {n} dni ({d})"],
["{n} day overdue ({d})", "{n} Tag überfällig ({d})", "{n} jour de retard ({d})", "{n} día de retraso ({d})", "{n} giorno di ritardo ({d})", "{n} dia de atraso ({d})", "{n} dag te laat ({d})", "{n} dzień zaległości ({d})"],
["{n} days overdue ({d})", "{n} Tage überfällig ({d})", "{n} jours de retard ({d})", "{n} días de retraso ({d})", "{n} giorni di ritardo ({d})", "{n} dias de atraso ({d})", "{n} dagen te laat ({d})", "{n} dni zaległości ({d})"],
["+ Plan", "+ Plan", "+ Plan", "+ Plan", "+ Piano", "+ Plano", "+ Plan", "+ Plan"],
["Restore", "Wiederherstellen", "Restaurer", "Restaurar", "Ripristina", "Restaurar", "Herstellen", "Przywróć"],
["Dismiss", "Ausblenden", "Masquer", "Descartar", "Nascondi", "Dispensar", "Verbergen", "Ukryj"],
["No subject", "Kein Fach", "Pas de matière", "Sin asignatura", "Nessuna materia", "Sem disciplina", "Geen vak", "Brak przedmiotu"],
["By status", "Nach Status", "Par statut", "Por estado", "Per stato", "Por estado", "Op status", "Wg statusu"],
["By subject", "Nach Fach", "Par matière", "Por asignatura", "Per materia", "Por disciplina", "Op vak", "Wg przedmiotu"],
["By due date", "Nach Fälligkeit", "Par échéance", "Por fecha de entrega", "Per scadenza", "Por data de entrega", "Op deadline", "Wg terminu"],
["Group", "Gruppieren", "Grouper", "Agrupar", "Raggruppa", "Agrupar", "Groeperen", "Grupuj"],
// ---- exams
["No exams.", "Keine Prüfungen.", "Aucun examen.", "Sin exámenes.", "Nessuna verifica.", "Sem exames.", "Geen toetsen.", "Brak sprawdzianów."],
["Upcoming exams", "Kommende Prüfungen", "Examens à venir", "Próximos exámenes", "Prossime verifiche", "Próximos exames", "Komende toetsen", "Nadchodzące sprawdziany"],
["Past exams", "Vergangene Prüfungen", "Examens passés", "Exámenes pasados", "Verifiche passate", "Exames passados", "Afgelopen toetsen", "Minione sprawdziany"],
["today", "heute", "aujourd'hui", "hoy", "oggi", "hoje", "vandaag", "dziś"],
["tomorrow", "morgen", "demain", "mañana", "domani", "amanhã", "morgen", "jutro"],
["yesterday", "gestern", "hier", "ayer", "ieri", "ontem", "gisteren", "wczoraj"],
["in {n} days", "in {n} Tagen", "dans {n} jours", "en {n} días", "tra {n} giorni", "em {n} dias", "over {n} dagen", "za {n} dni"],
["{n} days ago", "vor {n} Tagen", "il y a {n} jours", "hace {n} días", "{n} giorni fa", "há {n} dias", "{n} dagen geleden", "{n} dni temu"],
["Show past exams ({n})", "Vergangene anzeigen ({n})", "Afficher les passés ({n})", "Mostrar pasados ({n})", "Mostra passate ({n})", "Mostrar passados ({n})", "Toon afgelopen ({n})", "Pokaż minione ({n})"],
["Hide past exams", "Vergangene ausblenden", "Masquer les passés", "Ocultar pasados", "Nascondi passate", "Ocultar passados", "Verberg afgelopen", "Ukryj minione"],
["Date unknown", "Datum unbekannt", "Date inconnue", "Fecha desconocida", "Data sconosciuta", "Data desconhecida", "Datum onbekend", "Data nieznana"],
// ---- grades, absences
["Subject", "Fach", "Matière", "Asignatura", "Materia", "Disciplina", "Vak", "Przedmiot"],
["Subjects", "Fächer", "Matières", "Asignaturas", "Materie", "Disciplinas", "Vakken", "Przedmioty"],
["Average", "Durchschnitt", "Moyenne", "Promedio", "Media", "Média", "Gemiddelde", "Średnia"],
["Marks", "Noten", "Notes", "Notas", "Voti", "Notas", "Cijfers", "Oceny"],
["Grade calculator", "Notenrechner", "Calculateur de notes", "Calculadora de notas", "Calcolatore voti", "Calculadora de notas", "Cijfercalculator", "Kalkulator ocen"],
["Absences", "Fehlzeiten", "Absences", "Ausencias", "Assenze", "Faltas", "Absenties", "Nieobecności"],
["what if: 2, 3", "was wäre wenn: 2, 3", "et si : 2, 3", "y si: 2, 3", "e se: 2, 3", "e se: 2, 3", "stel: 2, 3", "a gdyby: 2, 3"],
["target avg", "Zieldurchschnitt", "moyenne visée", "media objetivo", "media obiettivo", "média alvo", "doelgemiddelde", "docelowa średnia"],
["Enter marks to try or a target average.", "Noten zum Ausprobieren oder einen Zieldurchschnitt eingeben.", "Saisissez des notes à tester ou une moyenne visée.", "Introduce notas para probar o un promedio objetivo.", "Inserisci voti da provare o una media obiettivo.", "Introduza notas para experimentar ou uma média alvo.", "Voer cijfers in om te proberen of een doelgemiddelde.", "Wpisz oceny do wypróbowania lub docelową średnią."],
["No grades synced (your school may not expose them).", "Keine Noten synchronisiert (deine Schule gibt sie evtl. nicht frei).", "Aucune note synchronisée (votre école ne les expose peut-être pas).", "No se han sincronizado notas (puede que tu centro no las ofrezca).", "Nessun voto sincronizzato (la tua scuola potrebbe non renderli disponibili).", "Sem notas sincronizadas (a sua escola pode não as disponibilizar).", "Geen cijfers gesynchroniseerd (je school toont ze misschien niet).", "Brak zsynchronizowanych ocen (szkoła może ich nie udostępniać)."],
["{n} total · {e} excused · {o} open · {d} days", "{n} gesamt · {e} entschuldigt · {o} offen · {d} Tage", "{n} au total · {e} justifiées · {o} ouvertes · {d} jours", "{n} en total · {e} justificadas · {o} abiertas · {d} días", "{n} in totale · {e} giustificate · {o} aperte · {d} giorni", "{n} no total · {e} justificadas · {o} em aberto · {d} dias", "{n} totaal · {e} geoorloofd · {o} open · {d} dagen", "{n} łącznie · {e} usprawiedliwione · {o} otwarte · {d} dni"],
["{n} absences · {e} excused · {o} open · {d} days", "{n} Fehlzeiten · {e} entschuldigt · {o} offen · {d} Tage", "{n} absences · {e} justifiées · {o} ouvertes · {d} jours", "{n} ausencias · {e} justificadas · {o} abiertas · {d} días", "{n} assenze · {e} giustificate · {o} aperte · {d} giorni", "{n} faltas · {e} justificadas · {o} em aberto · {d} dias", "{n} absenties · {e} geoorloofd · {o} open · {d} dagen", "{n} nieobecności · {e} usprawiedliwione · {o} otwarte · {d} dni"],
["Share", "Anteil", "Part", "Proporción", "Quota", "Percentagem", "Aandeel", "Udział"],
["Status", "Status", "Statut", "Estado", "Stato", "Estado", "Status", "Status"],
["excused", "entschuldigt", "excusé", "justificada", "giustificata", "justificada", "geoorloofd", "usprawiedliwiona"],
["open", "offen", "ouvert", "abierta", "aperta", "aberta", "open", "otwarta"],
// ---- study plan
["Your own study plan. You edit it here on the computer; it also shows (read-only) in Parent mode and in the offline copy.", "Dein eigener Lernplan. Du bearbeitest ihn hier am Computer; er erscheint auch (schreibgeschützt) im Eltern-Modus und in der Offline-Kopie.", "Votre propre plan d'étude. Vous le modifiez ici sur l'ordinateur ; il apparaît aussi (en lecture seule) en mode Parents et dans la copie hors ligne.", "Tu propio plan de estudio. Lo editas aquí en el ordenador; también aparece (solo lectura) en el modo Padres y en la copia sin conexión.", "Il tuo piano di studio personale. Lo modifichi qui sul computer; compare anche (in sola lettura) nella modalità Genitori e nella copia offline.", "O seu plano de estudo. Edita-o aqui no computador; também aparece (só de leitura) no modo Pais e na cópia offline.", "Je eigen studieplanning. Je bewerkt die hier op de computer; hij staat ook (alleen-lezen) in de ouderweergave en in de offlinekopie.", "Twój własny plan nauki. Edytujesz go tutaj na komputerze; widać go też (tylko do odczytu) w trybie dla rodziców i w kopii offline."],
["Nothing planned yet.", "Noch nichts geplant.", "Rien de prévu pour l'instant.", "Aún no hay nada planeado.", "Ancora niente in programma.", "Ainda nada planeado.", "Nog niets gepland.", "Jeszcze nic nie zaplanowano."],
["Show earlier ({n})", "Frühere anzeigen ({n})", "Afficher les précédents ({n})", "Mostrar anteriores ({n})", "Mostra precedenti ({n})", "Mostrar anteriores ({n})", "Toon eerdere ({n})", "Pokaż wcześniejsze ({n})"],
["Hide earlier ({n})", "Frühere ausblenden ({n})", "Masquer les précédents ({n})", "Ocultar anteriores ({n})", "Nascondi precedenti ({n})", "Ocultar anteriores ({n})", "Verberg eerdere ({n})", "Ukryj wcześniejsze ({n})"],
["Edit", "Bearbeiten", "Modifier", "Editar", "Modifica", "Editar", "Bewerken", "Edytuj"],
["What to do, e.g. Learn vocabulary unit 4", "Was zu tun ist, z. B. Vokabeln Lektion 4 lernen", "À faire, p. ex. apprendre le vocabulaire de l'unité 4", "Qué hacer, p. ej. aprender el vocabulario de la unidad 4", "Cosa fare, es. studiare il lessico dell'unità 4", "O que fazer, p. ex. aprender o vocabulário da unidade 4", "Wat te doen, bv. woordjes leren hoofdstuk 4", "Co zrobić, np. nauczyć się słówek z działu 4"],
["Notes (optional)", "Notizen (optional)", "Notes (facultatif)", "Notas (opcional)", "Note (facoltative)", "Notas (opcional)", "Notities (optioneel)", "Notatki (opcjonalnie)"],
["Save changes", "Änderungen speichern", "Enregistrer les modifications", "Guardar cambios", "Salva modifiche", "Guardar alterações", "Wijzigingen opslaan", "Zapisz zmiany"],
["Add to plan", "Zum Plan hinzufügen", "Ajouter au plan", "Añadir al plan", "Aggiungi al piano", "Adicionar ao plano", "Aan planning toevoegen", "Dodaj do planu"],
["Cancel", "Abbrechen", "Annuler", "Cancelar", "Annulla", "Cancelar", "Annuleren", "Anuluj"],
["Upcoming", "Kommend", "À venir", "Próximos", "In arrivo", "Próximos", "Komend", "Nadchodzące"],
["Last 7 days", "Letzte 7 Tage", "7 derniers jours", "Últimos 7 días", "Ultimi 7 giorni", "Últimos 7 dias", "Laatste 7 dagen", "Ostatnie 7 dni"],
// ---- people, changes, search
["Rooms", "Räume", "Salles", "Aulas", "Aule", "Salas", "Lokalen", "Sale"],
["Lessons", "Stunden", "Cours", "Clases", "Lezioni", "Aulas", "Lessen", "Lekcje"],
["Next lesson", "Nächste Stunde", "Prochain cours", "Próxima clase", "Prossima lezione", "Próxima aula", "Volgende les", "Następna lekcja"],
["No changes recorded yet. Changes appear after the second sync.", "Noch keine Änderungen erfasst. Änderungen erscheinen nach der zweiten Synchronisierung.", "Aucun changement enregistré. Les changements apparaissent après la deuxième synchronisation.", "Aún no hay cambios registrados. Los cambios aparecen tras la segunda sincronización.", "Nessuna modifica registrata. Le modifiche compaiono dopo la seconda sincronizzazione.", "Ainda sem alterações registadas. As alterações aparecem após a segunda sincronização.", "Nog geen wijzigingen vastgelegd. Wijzigingen verschijnen na de tweede synchronisatie.", "Brak zarejestrowanych zmian. Zmiany pojawiają się po drugiej synchronizacji."],
["Search everything…", "Alles durchsuchen…", "Tout rechercher…", "Buscar en todo…", "Cerca ovunque…", "Pesquisar tudo…", "Alles doorzoeken…", "Szukaj wszędzie…"],
// ---- settings, appearance
["School & login", "Schule & Anmeldung", "École et connexion", "Centro y acceso", "Scuola e accesso", "Escola e início de sessão", "School en inloggen", "Szkoła i logowanie"],
["Browser for login:", "Browser für die Anmeldung:", "Navigateur pour la connexion :", "Navegador para iniciar sesión:", "Browser per l'accesso:", "Navegador para iniciar sessão:", "Browser voor inloggen:", "Przeglądarka do logowania:"],
["Save", "Speichern", "Enregistrer", "Guardar", "Salva", "Guardar", "Opslaan", "Zapisz"],
["Choose another school / log in again", "Andere Schule wählen / erneut anmelden", "Choisir une autre école / se reconnecter", "Elegir otro centro / volver a iniciar sesión", "Scegli un'altra scuola / accedi di nuovo", "Escolher outra escola / iniciar sessão de novo", "Andere school kiezen / opnieuw inloggen", "Wybierz inną szkołę / zaloguj się ponownie"],
["Appearance", "Darstellung", "Apparence", "Apariencia", "Aspetto", "Aspeto", "Weergave", "Wygląd"],
["About", "Über", "À propos", "Acerca de", "Informazioni", "Sobre", "Over", "Informacje"],
["Language", "Sprache", "Langue", "Idioma", "Lingua", "Idioma", "Taal", "Język"],
["Mode", "Modus", "Mode", "Modo", "Modalità", "Modo", "Modus", "Tryb"],
["Theme", "Design", "Thème", "Tema", "Tema", "Tema", "Thema", "Motyw"],
["Accent color", "Akzentfarbe", "Couleur d'accent", "Color de acento", "Colore d'accento", "Cor de destaque", "Accentkleur", "Kolor akcentu"],
["System", "System", "Système", "Sistema", "Sistema", "Sistema", "Systeem", "Systemowy"],
["Light", "Hell", "Clair", "Claro", "Chiaro", "Claro", "Licht", "Jasny"],
["Dark", "Dunkel", "Sombre", "Oscuro", "Scuro", "Escuro", "Donker", "Ciemny"],
["Default", "Standard", "Par défaut", "Predeterminado", "Predefinito", "Predefinido", "Standaard", "Domyślny"],
["Paper", "Papier", "Papier", "Papel", "Carta", "Papel", "Papier", "Papier"],
["Ocean", "Ozean", "Océan", "Océano", "Oceano", "Oceano", "Oceaan", "Ocean"],
["Forest", "Wald", "Forêt", "Bosque", "Foresta", "Floresta", "Bos", "Las"],
["Midnight", "Mitternacht", "Minuit", "Medianoche", "Mezzanotte", "Meia-noite", "Middernacht", "Północ"],
["Rose", "Rosé", "Rose", "Rosa", "Rosa", "Rosa", "Roze", "Róż"],
["Graphite", "Graphit", "Graphite", "Grafito", "Grafite", "Grafite", "Grafiet", "Grafit"],
["Indigo", "Indigo", "Indigo", "Índigo", "Indaco", "Índigo", "Indigo", "Indygo"],
["Blue", "Blau", "Bleu", "Azul", "Blu", "Azul", "Blauw", "Niebieski"],
["Teal", "Petrol", "Sarcelle", "Verde azulado", "Verde acqua", "Verde-azulado", "Blauwgroen", "Morski"],
["Green", "Grün", "Vert", "Verde", "Verde", "Verde", "Groen", "Zielony"],
["Orange", "Orange", "Orange", "Naranja", "Arancione", "Laranja", "Oranje", "Pomarańczowy"],
["Pink", "Pink", "Rose vif", "Rosa fuerte", "Rosa acceso", "Rosa-choque", "Roze", "Różowy"],
["Purple", "Lila", "Violet", "Morado", "Viola", "Roxo", "Paars", "Fioletowy"],
["Slate", "Schiefer", "Ardoise", "Pizarra", "Ardesia", "Ardósia", "Leisteen", "Łupek"],
["Language, mode, theme and color are saved for your account on this device or server.", "Sprache, Modus, Design und Farbe werden für dein Konto auf diesem Gerät oder Server gespeichert.", "La langue, le mode, le thème et la couleur sont enregistrés pour votre compte sur cet appareil ou ce serveur.", "El idioma, el modo, el tema y el color se guardan para tu cuenta en este dispositivo o servidor.", "Lingua, modalità, tema e colore vengono salvati per il tuo account su questo dispositivo o server.", "O idioma, o modo, o tema e a cor são guardados para a sua conta neste dispositivo ou servidor.", "Taal, modus, thema en kleur worden voor je account op dit apparaat of deze server opgeslagen.", "Język, tryb, motyw i kolor są zapisywane dla Twojego konta na tym urządzeniu lub serwerze."],
// ---- settings: parent mode, watch
["Parent mode", "Eltern-Modus", "Mode parents", "Modo padres", "Modalità genitori", "Modo pais", "Ouderweergave", "Tryb dla rodziców"],
["Watch (PineTime / JWatch)", "Uhr (PineTime / JWatch)", "Montre (PineTime / JWatch)", "Reloj (PineTime / JWatch)", "Orologio (PineTime / JWatch)", "Relógio (PineTime / JWatch)", "Horloge (PineTime / JWatch)", "Zegarek (PineTime / JWatch)"],
["Find my watch", "Meine Uhr finden", "Trouver ma montre", "Buscar mi reloj", "Trova il mio orologio", "Encontrar o meu relógio", "Mijn horloge zoeken", "Znajdź mój zegarek"],
["Send test", "Test senden", "Envoyer un test", "Enviar prueba", "Invia test", "Enviar teste", "Test sturen", "Wyślij test"],
["Forget", "Entfernen", "Oublier", "Olvidar", "Dimentica", "Esquecer", "Vergeten", "Zapomnij"],
["Turn on (starts at login)", "Einschalten (startet bei der Anmeldung)", "Activer (démarre à la connexion)", "Activar (se inicia al iniciar sesión)", "Attiva (parte all'accesso)", "Ativar (inicia com a sessão)", "Inschakelen (start bij het inloggen)", "Włącz (uruchamia się po zalogowaniu)"],
["Turn off", "Ausschalten", "Désactiver", "Desactivar", "Disattiva", "Desativar", "Uitschakelen", "Wyłącz"],
["New link", "Neuer Link", "Nouveau lien", "Nuevo enlace", "Nuovo link", "Novo link", "Nieuwe link", "Nowy link"],
["Copy browser link", "Browser-Link kopieren", "Copier le lien navigateur", "Copiar enlace del navegador", "Copia link per il browser", "Copiar ligação do navegador", "Browserlink kopiëren", "Kopiuj link do przeglądarki"],
["Copy wall-display link", "Wanddisplay-Link kopieren", "Copier le lien d'affichage mural", "Copiar enlace de pantalla de pared", "Copia link per display a muro", "Copiar ligação do ecrã de parede", "Wanddisplay-link kopiëren", "Kopiuj link do wyświetlacza ściennego"],
["School:", "Schule:", "École :", "Centro:", "Scuola:", "Escola:", "School:", "Szkoła:"],
["Show on the Parent page", "Auf der Eltern-Seite anzeigen", "Afficher sur la page Parents", "Mostrar en la página de padres", "Mostra nella pagina genitori", "Mostrar na página dos pais", "Tonen op de ouderpagina", "Pokaż na stronie dla rodziców"],
// ---- updates
["Updates", "Updates", "Mises à jour", "Actualizaciones", "Aggiornamenti", "Atualizações", "Updates", "Aktualizacje"],
["Check for updates", "Nach Updates suchen", "Rechercher des mises à jour", "Buscar actualizaciones", "Cerca aggiornamenti", "Procurar atualizações", "Controleren op updates", "Sprawdź aktualizacje"],
["Checking…", "Prüfe…", "Vérification…", "Comprobando…", "Controllo…", "A verificar…", "Controleren…", "Sprawdzanie…"],
["Version {v}", "Version {v}", "Version {v}", "Versión {v}", "Versione {v}", "Versão {v}", "Versie {v}", "Wersja {v}"],
["You're up to date.", "Du bist auf dem neuesten Stand.", "Vous êtes à jour.", "Estás al día.", "Sei aggiornato.", "Está atualizado.", "Je bent up-to-date.", "Masz najnowszą wersję."],
["Version {v} is available.", "Version {v} ist verfügbar.", "La version {v} est disponible.", "La versión {v} está disponible.", "La versione {v} è disponibile.", "A versão {v} está disponível.", "Versie {v} is beschikbaar.", "Dostępna jest wersja {v}."],
["Release notes", "Versionshinweise", "Notes de version", "Notas de la versión", "Note di rilascio", "Notas de versão", "Release-opmerkingen", "Informacje o wydaniu"],
["Download update", "Update herunterladen", "Télécharger la mise à jour", "Descargar actualización", "Scarica aggiornamento", "Transferir atualização", "Update downloaden", "Pobierz aktualizację"],
["Install update", "Update installieren", "Installer la mise à jour", "Instalar actualización", "Installa aggiornamento", "Instalar atualização", "Update installeren", "Zainstaluj aktualizację"],
["Downloading…", "Lade herunter…", "Téléchargement…", "Descargando…", "Download in corso…", "A transferir…", "Downloaden…", "Pobieranie…"],
["Saved to {p}. Quit SSC and unpack it over the old one.", "Gespeichert unter {p}. SSC beenden und über die alte Version entpacken.", "Enregistré dans {p}. Quittez SSC et décompressez par-dessus l'ancienne version.", "Guardado en {p}. Cierra SSC y descomprímelo sobre la versión anterior.", "Salvato in {p}. Chiudi SSC e decomprimi sopra la vecchia versione.", "Guardado em {p}. Feche o SSC e descompacte por cima da versão antiga.", "Opgeslagen in {p}. Sluit SSC af en pak het over de oude versie uit.", "Zapisano w {p}. Zamknij SSC i rozpakuj nad starą wersją."],
["The server is updating and restarts in a moment…", "Der Server wird aktualisiert und startet gleich neu…", "Le serveur se met à jour et redémarre dans un instant…", "El servidor se está actualizando y se reiniciará en un momento…", "Il server si sta aggiornando e si riavvierà a breve…", "O servidor está a atualizar e reinicia em instantes…", "De server wordt bijgewerkt en start zo opnieuw op…", "Serwer się aktualizuje i zaraz uruchomi się ponownie…"],
// ---- parent page, login, chrome
["School overview", "Schulübersicht", "Aperçu scolaire", "Resumen escolar", "Panoramica scolastica", "Resumo escolar", "Schooloverzicht", "Przegląd szkolny"],
["Offline - showing the last data received ({d}).", "Offline – zuletzt empfangene Daten ({d}).", "Hors ligne – dernières données reçues ({d}).", "Sin conexión: se muestran los últimos datos recibidos ({d}).", "Offline: ultimi dati ricevuti ({d}).", "Offline – a mostrar os últimos dados recebidos ({d}).", "Offline – laatst ontvangen gegevens ({d}).", "Offline – ostatnio odebrane dane ({d})."],
["Offline - showing the last data received.", "Offline – zuletzt empfangene Daten.", "Hors ligne – dernières données reçues.", "Sin conexión: se muestran los últimos datos recibidos.", "Offline: ultimi dati ricevuti.", "Offline – a mostrar os últimos dados recebidos.", "Offline – laatst ontvangen gegevens.", "Offline – ostatnio odebrane dane."],
["Updated {d}", "Aktualisiert {d}", "Mis à jour {d}", "Actualizado {d}", "Aggiornato {d}", "Atualizado {d}", "Bijgewerkt {d}", "Zaktualizowano {d}"],
["Homework due soon", "Bald fällige Hausaufgaben", "Devoirs bientôt dus", "Deberes próximos", "Compiti in scadenza", "Trabalhos a vencer", "Huiswerk binnenkort", "Zadania wkrótce"],
["Homework due", "Fällige Hausaufgaben", "Devoirs à rendre", "Deberes pendientes", "Compiti in scadenza", "Trabalhos por entregar", "Huiswerk met deadline", "Zadania do oddania"],
["Nothing open 🎉", "Nichts offen 🎉", "Rien d'ouvert 🎉", "Nada pendiente 🎉", "Niente di aperto 🎉", "Nada em aberto 🎉", "Niets open 🎉", "Nic otwartego 🎉"],
["No homework.", "Keine Hausaufgaben.", "Pas de devoirs.", "Sin deberes.", "Nessun compito.", "Sem trabalhos de casa.", "Geen huiswerk.", "Brak zadań domowych."],
["No timetable data yet.", "Noch keine Stundenplandaten.", "Pas encore de données d'emploi du temps.", "Aún no hay datos del horario.", "Ancora nessun dato dell'orario.", "Ainda sem dados do horário.", "Nog geen roostergegevens.", "Brak danych planu lekcji."],
["Latest changes", "Letzte Änderungen", "Derniers changements", "Últimos cambios", "Ultime modifiche", "Últimas alterações", "Laatste wijzigingen", "Ostatnie zmiany"],
["No recent changes.", "Keine neuen Änderungen.", "Aucun changement récent.", "Sin cambios recientes.", "Nessuna modifica recente.", "Sem alterações recentes.", "Geen recente wijzigingen.", "Brak ostatnich zmian."],
["No changes.", "Keine Änderungen.", "Aucun changement.", "Sin cambios.", "Nessuna modifica.", "Sem alterações.", "Geen wijzigingen.", "Brak zmian."],
["No grades.", "Keine Noten.", "Aucune note.", "Sin notas.", "Nessun voto.", "Sem notas.", "Geen cijfers.", "Brak ocen."],
["Nothing planned.", "Nichts geplant.", "Rien de prévu.", "Nada previsto.", "Niente in programma.", "Nada planeado.", "Niets gepland.", "Nic nie zaplanowano."],
["School's out", "Schulschluss", "L'école est finie", "Fin de clases", "Scuola finita", "Fim das aulas", "School is uit", "Koniec lekcji"],
["No lessons today", "Heute kein Unterricht", "Pas de cours aujourd'hui", "Hoy no hay clases", "Oggi nessuna lezione", "Hoje não há aulas", "Vandaag geen lessen", "Dziś brak lekcji"],
["Tomorrow starts at {t}", "Morgen geht es um {t} los", "Demain, début à {t}", "Mañana empieza a las {t}", "Domani si inizia alle {t}", "Amanhã começa às {t}", "Morgen begint om {t}", "Jutro start o {t}"],
["Sign in to see the school overview.", "Melde dich an, um die Schulübersicht zu sehen.", "Connectez-vous pour voir l'aperçu scolaire.", "Inicia sesión para ver el resumen escolar.", "Accedi per vedere la panoramica scolastica.", "Inicie sessão para ver o resumo escolar.", "Log in om het schooloverzicht te zien.", "Zaloguj się, aby zobaczyć przegląd szkolny."],
["Account", "Konto", "Compte", "Cuenta", "Account", "Conta", "Account", "Konto"],
["Password", "Passwort", "Mot de passe", "Contraseña", "Password", "Palavra-passe", "Wachtwoord", "Hasło"],
["Sign in", "Anmelden", "Se connecter", "Iniciar sesión", "Accedi", "Iniciar sessão", "Inloggen", "Zaloguj"],
["Continue as guest", "Als Gast fortfahren", "Continuer en tant qu'invité", "Continuar como invitado", "Continua come ospite", "Continuar como convidado", "Doorgaan als gast", "Kontynuuj jako gość"],
["Wrong name or password.", "Falscher Name oder falsches Passwort.", "Nom ou mot de passe incorrect.", "Nombre o contraseña incorrectos.", "Nome o password errati.", "Nome ou palavra-passe incorretos.", "Onjuiste naam of wachtwoord.", "Błędna nazwa lub hasło."],
["Sign-in failed", "Anmeldung fehlgeschlagen", "Échec de la connexion", "Error al iniciar sesión", "Accesso non riuscito", "Falha no início de sessão", "Inloggen mislukt", "Logowanie nie powiodło się"],
["Log out", "Abmelden", "Se déconnecter", "Cerrar sesión", "Esci", "Terminar sessão", "Uitloggen", "Wyloguj"],
["Admin panel", "Admin-Bereich", "Panneau d'administration", "Panel de administración", "Pannello di amministrazione", "Painel de administração", "Beheerpaneel", "Panel administracyjny"]
// ---- updates are appended by core.load_page() consumers via SSCUI.extend() (see the updater card)
];

// ------------------------------------------------------------------ translation tables
var exact = {}, patterns = [];

function addRows(rows) {
  rows.forEach(function (r) {
    var key = r[0];
    ORDER.forEach(function (lang, i) { if (r[i + 1]) (exact[lang] = exact[lang] || {})[key] = r[i + 1]; });
    if (key.indexOf("{") >= 0) {
      var names = [];
      var re = "^" + key.replace(/[.*+?^$()|[\]\\]/g, "\\$&").replace(/\{(\w+)\}/g, function (_, n) { names.push(n); return "nabeo".indexOf(n) >= 0 && n.length === 1 ? "(\\d+)" : "(.+?)"; }) + "$";   // counts are digits only: "{n} done" must not swallow a sentence
      patterns.push({ key: key, re: new RegExp(re), names: names, weight: key.replace(/\{\w+\}/g, "").length });
    }
  });
  patterns.sort(function (a, b) { return b.weight - a.weight; });      // the most specific wording wins
}
addRows(D);

var lang = "en";

function tpl(str, vars) {
  return vars ? String(str).replace(/\{(\w+)\}/g, function (m, n) { return vars[n] != null ? vars[n] : m; }) : str;
}

/* T("due in {n} days ({d})", {n: 3, d: "11-24"})  -> translated text with the values filled in */
function T(key, vars) {
  var s = (exact[lang] && exact[lang][key]) || key;
  return tpl(s, vars);
}

/* tr(text): translate a finished text (as it appears on the page), keeping surrounding white space */
function tr(text) {
  if (lang === "en" || !text) return text;
  var m = /^(\s*)([\s\S]*?)(\s*)$/.exec(text), core = m[2];
  if (!core) return text;
  var table = exact[lang];
  if (!table) return text;
  if (table[core] !== undefined) return m[1] + table[core] + m[3];
  for (var i = 0; i < patterns.length; i++) {
    var p = patterns[i], mm = p.re.exec(core);
    if (mm && table[p.key]) {
      var vars = {};
      p.names.forEach(function (n, j) { vars[n] = mm[j + 1]; });
      return m[1] + tpl(table[p.key], vars) + m[3];
    }
  }
  return text;
}

// ------------------------------------------------------------------ preferences + appearance
var MODES = ["system", "light", "dark"];
var PALETTES = {
  default: null,
  paper:    { name: "Paper",    light: { bg: "#f6f1e9", surface: "#fffdf8", surface2: "#efe8db", line: "#e3dac8", side: "#fbf7ef", text: "#2a2118", muted: "#7a6c58" },
                                dark:  { bg: "#17130e", surface: "#211b14", surface2: "#2a231a", line: "#3a3022", side: "#1c1711", text: "#f3ece0", muted: "#b3a48c" } },
  ocean:    { name: "Ocean",    light: { bg: "#eef4f9", surface: "#ffffff", surface2: "#e4eef6", line: "#d5e3ee", side: "#f7fbfe", text: "#0f2233", muted: "#577089" },
                                dark:  { bg: "#08121c", surface: "#0e1c2b", surface2: "#14283b", line: "#1d3650", side: "#0b1724", text: "#e6f1fb", muted: "#8fb0cc" } },
  forest:   { name: "Forest",   light: { bg: "#eff5f0", surface: "#ffffff", surface2: "#e3eee6", line: "#d3e3d8", side: "#f7fbf8", text: "#14261b", muted: "#5b7564" },
                                dark:  { bg: "#0a140e", surface: "#101e15", surface2: "#16291d", line: "#214030", side: "#0d1a12", text: "#e6f3ea", muted: "#8db39a" } },
  midnight: { name: "Midnight", light: null,
                                dark:  { bg: "#000000", surface: "#0b0b10", surface2: "#14141c", line: "#22222e", side: "#050507", text: "#f1f1f6", muted: "#8d8da0" } },
  rose:     { name: "Rose",     light: { bg: "#fbf1f4", surface: "#ffffff", surface2: "#f6e4ea", line: "#ecd3dc", side: "#fef8fa", text: "#2b1620", muted: "#85586a" },
                                dark:  { bg: "#170b10", surface: "#22111a", surface2: "#2d1823", line: "#402230", side: "#1d0e15", text: "#f8e8ef", muted: "#c093a8" } },
  graphite: { name: "Graphite", light: { bg: "#f2f2f3", surface: "#ffffff", surface2: "#e8e8ea", line: "#dcdcdf", side: "#fafafa", text: "#18181b", muted: "#6b6b73" },
                                dark:  { bg: "#101012", surface: "#19191c", surface2: "#222226", line: "#303036", side: "#141416", text: "#f2f2f4", muted: "#9a9aa3" } }
};
var ACCENTS = {   // id: [light, dark, companion colour used in gradients]
  indigo: ["#4f46e5", "#8e90ff", "#0ea5a4", "Indigo"], blue: ["#2563eb", "#60a5fa", "#06b6d4", "Blue"], teal: ["#0d9488", "#2dd4bf", "#6366f1", "Teal"],
  green: ["#16a34a", "#4ade80", "#0ea5e9", "Green"], orange: ["#ea580c", "#fb923c", "#e11d48", "Orange"], pink: ["#e11d48", "#fb7185", "#f59e0b", "Pink"],
  purple: ["#9333ea", "#c084fc", "#ec4899", "Purple"], slate: ["#475569", "#94a3b8", "#0ea5a4", "Slate"]
};
var DEFAULTS = { lang: "", theme: "system", palette: "default", accent: "indigo" };
var prefs = {}, listeners = [], KEY = "ssc-prefs";

function store(get, key, val) {
  try { if (get) return root.localStorage.getItem(key); root.localStorage.setItem(key, val); } catch (e) { return null; }
}

function guessLang() {
  var n = (root.navigator && (root.navigator.languages ? root.navigator.languages[0] : root.navigator.language)) || "en";
  var c = String(n).toLowerCase().split("-")[0];
  return LANGS[c] ? c : "en";
}

function clean(p) {
  var o = {};
  if (p && LANGS[p.lang]) o.lang = p.lang;
  if (p && MODES.indexOf(p.theme) >= 0) o.theme = p.theme;
  if (p && Object.prototype.hasOwnProperty.call(PALETTES, p.palette)) o.palette = p.palette;
  if (p && ACCENTS[p.accent]) o.accent = p.accent;
  return o;
}

function loadLocal() {
  var raw = store(true, KEY), p = {};
  try { p = JSON.parse(raw || "{}") || {}; } catch (e) { p = {}; }
  var legacy = store(true, "ssc-theme");                       // older versions kept only the mode
  if (!p.theme && (legacy === "light" || legacy === "dark")) p.theme = legacy;
  return clean(p);
}

function effectiveMode() {
  if (prefs.theme === "light" || prefs.theme === "dark") return prefs.theme;
  return root.matchMedia && root.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function hexToRgba(hex, a) {
  var n = parseInt(hex.slice(1), 16);
  return "rgba(" + (n >> 16) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + a + ")";
}

function applyAppearance() {
  if (!root.document) return;
  var doc = root.document, el = doc.documentElement, mode = effectiveMode(), st = el.style;
  el.setAttribute("data-theme", mode);
  el.lang = lang;
  var names = ["bg", "surface", "surface2", "line", "side", "text", "muted"];
  var pal = PALETTES[prefs.palette || "default"], v = pal && pal[mode];
  var map = { bg: ["--bg"], surface: ["--surface", "--card"], surface2: ["--surface2"], line: ["--line"], side: ["--side"], text: ["--text", "--fg"], muted: ["--muted", "--mut"] };
  names.forEach(function (n) { map[n].forEach(function (cssVar) { if (v) st.setProperty(cssVar, v[n]); else st.removeProperty(cssVar); }); });
  var a = ACCENTS[prefs.accent || "indigo"], c = mode === "dark" ? a[1] : a[0];
  if (prefs.accent && prefs.accent !== "indigo") {
    st.setProperty("--accent", c); st.setProperty("--acc", c); st.setProperty("--accent2", a[2]);
    st.setProperty("--accent-soft", hexToRgba(c, mode === "dark" ? 0.16 : 0.11));
    st.setProperty("--on-accent", mode === "dark" ? "#0b0f17" : "#fff");
  } else {
    ["--accent", "--acc", "--accent2", "--accent-soft", "--on-accent"].forEach(function (k) { st.removeProperty(k); });
  }
}

function translateAll() {
  if (!root.document || !root.document.body) return;
  walk(root.document.body);
  root.document.title = tr(root.document.title_o || (root.document.title_o = root.document.title));
}

function setPrefs(patch, opts) {
  var next = {};
  Object.keys(DEFAULTS).forEach(function (k) { next[k] = prefs[k]; });
  var c = clean(patch);
  Object.keys(c).forEach(function (k) { next[k] = c[k]; });
  var langChanged = c.lang && c.lang !== prefs.lang;
  prefs = next;
  lang = prefs.lang || guessLang();
  applyAppearance();
  if (langChanged || (opts && opts.retranslate)) translateAll();
  if (!opts || !opts.silent) {
    store(false, KEY, JSON.stringify(prefs));
    save();
  }
  listeners.forEach(function (f) { try { f(prefs); } catch (e) { /* a page callback must not break the others */ } });
}

var saveTimer = null;
/* Only pages that say so (window.SSC_PREFS_API) talk to /api/prefs: the read-only Parent link server treats every unknown
   request as a failed guess of the secret link, so it must never be asked. */
function remoteOk() { return !!(root.document && root.SSC_PREFS_API === true && root.fetch && !root.__SNAPSHOT__ && root.location.protocol !== "file:"); }

function save() {                                               // best effort: the page may have no server (offline copy, demo)
  if (!remoteOk()) return;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(function () {
    try { root.fetch("/api/prefs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(clean(prefs)) }).catch(function () {}); } catch (e) { /* offline */ }
  }, 300);
}

function loadRemote() {
  if (!remoteOk()) return;
  root.fetch("/api/prefs", { cache: "no-store" }).then(function (r) { return r.ok ? r.json() : null; }).then(function (p) {
    if (p && Object.keys(clean(p)).length) { setPrefs(clean(p), { silent: true, retranslate: true }); store(false, KEY, JSON.stringify(prefs)); }
  }).catch(function () {});
}

// ------------------------------------------------------------------ DOM translation
var SKIP = { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, CODE: 1, PRE: 1, NOSCRIPT: 1 };
var ATTRS = ["placeholder", "title", "aria-label", "alt"];

function xlateText(n) {
  var cur = n.data;
  if (n.__t === undefined || cur !== n.__t) n.__o = cur;        // new text, or the page changed it: that is the new English source
  var out = tr(n.__o);
  n.__t = out;
  if (out !== cur) n.data = out;
}

function xlateAttrs(el) {
  for (var i = 0; i < ATTRS.length; i++) {
    var a = ATTRS[i];
    if (!el.hasAttribute || !el.hasAttribute(a)) continue;
    var key = "__o_" + a, cur = el.getAttribute(a);
    if (el[key] === undefined || el["__t_" + a] !== cur) el[key] = cur;
    var out = tr(el[key]);
    el["__t_" + a] = out;
    if (out !== cur) el.setAttribute(a, out);
  }
}

function walk(node) {
  if (!node) return;
  if (node.nodeType === 3) { xlateText(node); return; }
  if (node.nodeType !== 1 || SKIP[node.tagName] || (node.closest && node.closest("[translate=no]"))) return;
  xlateAttrs(node);
  for (var c = node.firstChild; c; c = c.nextSibling) walk(c);
}

var pending = [], scheduled = false;
function flush() {
  scheduled = false;
  var list = pending; pending = [];
  list.forEach(function (n) { if (n.isConnected !== false) walk(n); });
}
function startObserver() {
  if (!root.MutationObserver || !root.document.body) return;
  new root.MutationObserver(function (muts) {
    muts.forEach(function (m) {
      if (m.type === "characterData") pending.push(m.target);
      else m.addedNodes.forEach(function (n) { pending.push(n); });
    });
    if (!scheduled) { scheduled = true; (root.requestAnimationFrame || root.setTimeout)(flush); }
  }).observe(root.document.body, { childList: true, subtree: true, characterData: true });
}

// ------------------------------------------------------------------ the settings panel (dialog or inline)
function h(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

function panelHtml() {
  var mode = prefs.theme || "system", pal = prefs.palette || "default", acc = prefs.accent || "indigo", cur = prefs.lang || lang;
  var langs = Object.keys(LANGS).map(function (k) { return '<option value="' + k + '"' + (k === cur ? " selected" : "") + ">" + h(LANGS[k]) + "</option>"; }).join("");
  var modes = MODES.map(function (m) { return '<button type="button" class="sscui-seg' + (m === mode ? " on" : "") + '" data-ssc-mode="' + m + '">' + h({ system: "System", light: "Light", dark: "Dark" }[m]) + "</button>"; }).join("");
  var m2 = effectiveMode();
  var pals = Object.keys(PALETTES).map(function (k) {
    var p = PALETTES[k], v = (p && (p[m2] || p.light || p.dark)) || (m2 === "dark" ? { bg: "#0b0f17", surface: "#141a26", text: "#eef1f8" } : { bg: "#f3f5fb", surface: "#ffffff", text: "#131826" });
    return '<button type="button" class="sscui-pal' + (k === pal ? " on" : "") + '" data-ssc-palette="' + k + '" style="--pb:' + v.bg + ";--ps:" + v.surface + ";--pt:" + v.text + '"><i></i><span>' + h(k === "default" ? "Default" : p.name) + "</span></button>";
  }).join("");
  var accs = Object.keys(ACCENTS).map(function (k) {
    var a = ACCENTS[k];
    return '<button type="button" class="sscui-dot' + (k === acc ? " on" : "") + '" data-ssc-accent="' + k + '" title="' + h(a[3]) + '" aria-label="' + h(a[3]) + '" style="background:' + (m2 === "dark" ? a[1] : a[0]) + '"></button>';
  }).join("");
  return '<div class="sscui-row"><label>' + h("Language") + '</label><select data-ssc-lang>' + langs + '</select></div>' +
         '<div class="sscui-row"><label>' + h("Mode") + '</label><div class="sscui-segs">' + modes + '</div></div>' +
         '<div class="sscui-row"><label>' + h("Theme") + '</label><div class="sscui-pals">' + pals + '</div></div>' +
         '<div class="sscui-row"><label>' + h("Accent color") + '</label><div class="sscui-dots">' + accs + '</div></div>' +
         '<p class="sscui-note">' + h("Language, mode, theme and color are saved for your account on this device or server.") + '</p>';
}

function bindPanel(box) {
  box.innerHTML = panelHtml();
  translateAll();
  var refresh = function () { bindPanel(box); };
  var sel = box.querySelector("[data-ssc-lang]");
  if (sel) sel.onchange = function () { setPrefs({ lang: sel.value }); refresh(); };
  [].forEach.call(box.querySelectorAll("[data-ssc-mode]"), function (b) { b.onclick = function () { setPrefs({ theme: b.getAttribute("data-ssc-mode") }); refresh(); }; });
  [].forEach.call(box.querySelectorAll("[data-ssc-palette]"), function (b) { b.onclick = function () { setPrefs({ palette: b.getAttribute("data-ssc-palette") }); refresh(); }; });
  [].forEach.call(box.querySelectorAll("[data-ssc-accent]"), function (b) { b.onclick = function () { setPrefs({ accent: b.getAttribute("data-ssc-accent") }); refresh(); }; });
}

function injectCss() {
  var d = root.document;
  if (d.getElementById("sscui-css")) return;
  var s = d.createElement("style"); s.id = "sscui-css";
  s.textContent = ".sscui-bg{position:fixed;inset:0;z-index:200;background:rgba(5,8,16,.5);backdrop-filter:blur(3px);display:grid;place-items:center;padding:16px}" +
    ".sscui-box{width:min(520px,100%);max-height:92vh;overflow:auto;background:var(--surface,var(--card,#fff));color:var(--text,var(--fg,#111));border:1px solid var(--line,#ddd);border-radius:20px;padding:20px 22px 22px;box-shadow:0 24px 60px rgba(0,0,0,.35)}" +
    ".sscui-box h3{margin:0 0 12px;font-size:19px;display:flex;align-items:center;justify-content:space-between}" +
    ".sscui-x{border:1px solid var(--line,#ddd);background:transparent;color:inherit;border-radius:10px;width:34px;height:34px;cursor:pointer;font-size:15px}" +
    ".sscui-row{margin:14px 0}.sscui-row>label{display:block;font-size:12px;text-transform:uppercase;letter-spacing:.08em;font-weight:700;color:var(--muted,var(--mut,#667));margin-bottom:7px}" +
    ".sscui-row select{font:inherit;padding:8px 12px;border-radius:10px;border:1px solid var(--line,#ddd);background:var(--surface,var(--card,#fff));color:inherit;min-width:200px}" +
    ".sscui-segs{display:inline-flex;border:1px solid var(--line,#ddd);border-radius:11px;overflow:hidden}" +
    ".sscui-seg{font:inherit;font-size:14px;padding:8px 16px;border:0;background:transparent;color:inherit;cursor:pointer}.sscui-seg.on{background:var(--accent,var(--acc,#4f46e5));color:var(--on-accent,#fff);font-weight:650}" +
    ".sscui-pals{display:grid;grid-template-columns:repeat(auto-fill,minmax(104px,1fr));gap:9px}" +
    ".sscui-pal{font:inherit;font-size:13px;display:flex;flex-direction:column;gap:7px;align-items:stretch;padding:8px;border-radius:13px;border:2px solid var(--line,#ddd);background:var(--surface,var(--card,#fff));color:inherit;cursor:pointer;text-align:left}" +
    ".sscui-pal i{display:block;height:34px;border-radius:8px;background:linear-gradient(90deg,var(--pb) 0 38%,var(--ps) 38% 100%);box-shadow:inset 0 0 0 1px rgba(128,128,128,.28)}" +
    ".sscui-pal.on{border-color:var(--accent,var(--acc,#4f46e5))}" +
    ".sscui-dots{display:flex;gap:10px;flex-wrap:wrap}.sscui-dot{width:34px;height:34px;border-radius:50%;border:3px solid transparent;cursor:pointer;box-shadow:inset 0 0 0 1px rgba(0,0,0,.18)}.sscui-dot.on{border-color:var(--surface,var(--card,#fff));outline:3px solid var(--accent,var(--acc,#4f46e5))}" +
    ".sscui-note{font-size:12.5px;color:var(--muted,var(--mut,#667));margin:14px 0 0}";
  d.head.appendChild(s);
}

var dialog = null;
function openDialog() {
  injectCss();
  if (dialog) return;
  var d = root.document, bg = d.createElement("div");
  bg.className = "sscui-bg"; bg.setAttribute("role", "dialog"); bg.setAttribute("aria-modal", "true");
  bg.innerHTML = '<div class="sscui-box"><h3><span>' + h("Appearance") + '</span><button class="sscui-x" type="button" aria-label="' + h("Close") + '">✕</button></h3><div id="sscui-body"></div></div>';
  var close = function () { bg.remove(); dialog = null; d.removeEventListener("keydown", esc); };
  var esc = function (e) { if (e.key === "Escape") close(); };
  bg.onclick = function (e) { if (e.target === bg) close(); };
  bg.querySelector(".sscui-x").onclick = close;
  d.addEventListener("keydown", esc);
  d.body.appendChild(bg); dialog = bg;
  bindPanel(bg.querySelector("#sscui-body"));
}
function mount(el) { if (!el) return; injectCss(); bindPanel(el); }

// ------------------------------------------------------------------ public API
var API = {
  LANGS: LANGS, ACCENTS: ACCENTS, PALETTES: PALETTES, MODES: MODES,
  t: T, tr: tr,
  lang: function () { return lang; },
  locale: function () { return LOCALES[lang] || "en-GB"; },
  prefs: function () { return JSON.parse(JSON.stringify(prefs)); },
  set: setPrefs, open: openDialog, mount: mount,
  onChange: function (f) { listeners.push(f); },
  extend: function (rows) { addRows(rows); if (root.document) translateAll(); },        // features add their own texts
  _walk: walk
};
root.SSCUI = API;

// first paint: apply what this browser remembers, then let the server's copy (per account) win
prefs = loadLocal();
lang = prefs.lang || guessLang();
if (root.document) {
  try { applyAppearance(); } catch (e) { /* no DOM yet */ }
  if (root.matchMedia) {
    var mq = root.matchMedia("(prefers-color-scheme: dark)");
    (mq.addEventListener ? mq.addEventListener.bind(mq, "change") : mq.addListener.bind(mq))(function () { if (prefs.theme === "system" || !prefs.theme) { applyAppearance(); listeners.forEach(function (f) { f(prefs); }); } });
  }
  var ready = function () { translateAll(); startObserver(); loadRemote(); };
  if (root.document.readyState === "loading") root.document.addEventListener("DOMContentLoaded", ready); else ready();
}
if (typeof module !== "undefined" && module.exports) module.exports = API;
})(typeof window !== "undefined" ? window : globalThis);
