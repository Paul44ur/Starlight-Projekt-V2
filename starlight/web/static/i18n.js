// The pages in German: when the browser speaks German (or "Deutsch" is chosen in Settings),
// the interface's words are swapped as they appear. The conversation itself - your
// messages and Starlight's answers - is never touched; the model answers in your language.
(function () {
  function pick() {
    try {
      const chosen = localStorage.getItem("starlight-lang");
      if (chosen === "en" || chosen === "de") return chosen;
    } catch (e) { /* private window */ }
    return (navigator.language || "en").toLowerCase().startsWith("de") ? "de" : "en";
  }
  window.starlightLanguage = pick();
  if (window.starlightLanguage !== "de") return;
  document.documentElement.lang = "de";

  const DE = {
    // the pages and their links
    "Chat": "Chat", "Memory": "Gedächtnis", "Activity": "Aktivität", "Automations": "Automationen",
    "Settings": "Einstellungen", "Theme": "Farben",
    "Skip to the message box": "Zum Eingabefeld springen",
    // the model card (settings)
    "The model Starlight answers with. Saved in config/launcher.json.":
      "Das Modell, mit dem Starlight antwortet. Gespeichert in config/launcher.json.",
    "In use now": "Gerade im Einsatz", "Context size": "Kontextgröße", "Automatic": "Automatisch",
    "Restart": "Neu starten", "Restart Starlight": "Starlight neu starten",
    "whichever model the server has loaded": "das Modell, das der Server gerade geladen hat",
    "Saved. It applies after Starlight restarts.": "Gespeichert. Es gilt nach einem Neustart von Starlight.",
    "The model is changed at the PC.": "Das Modell wird am PC geändert.",
    "config/starlight.local.yaml sets a model: a setting in it wins over this page.":
      "config/starlight.local.yaml legt ein Modell fest: Diese Einstellung hat Vorrang vor dieser Seite.",
    "No Starlight window is running: restart Starlight yourself.":
      "Es läuft kein Starlight-Fenster: Starte Starlight selbst neu.",
    "New models are downloaded in the Starlight window (Settings there).":
      "Neue Modelle werden im Starlight-Fenster geladen (dort unter Einstellungen).",
    "Restart Starlight now?": "Starlight jetzt neu starten?",
    "The chat stops for about a minute while the model loads.":
      "Der Chat steht etwa eine Minute still, während das Modell lädt.",
    "Restart now?": "Jetzt neu starten?",
    "An answer is being written and will be cut off.": "Eine Antwort wird gerade geschrieben und wird abgebrochen.",
    "Starlight restarts now. This page works again in about a minute.":
      "Starlight startet jetzt neu. Diese Seite geht in etwa einer Minute wieder.",
    "Starlight runs with its built-in test model: models are set up in the Starlight window.":
      "Starlight läuft mit dem eingebauten Testmodell: Modelle richtest du im Starlight-Fenster ein.",
    "There is no model file in the models folder yet.": "Im Ordner „models“ liegt noch keine Modelldatei.",
    "The address of the model server is not set.": "Die Adresse des Modellservers ist nicht eingetragen.",
    "config/launcher.json has a mistake: fix it first.":
      "In config/launcher.json steckt ein Fehler: Behebe ihn zuerst.",
    "That model is not in the list.": "Dieses Modell steht nicht in der Liste.",
    "That context size is not one of the offered sizes.": "Diese Kontextgröße gehört nicht zu den angebotenen.",
    "That model file is gone.": "Diese Modelldatei gibt es nicht mehr.",
    "no Starlight window is running: restart Starlight yourself":
      "Es läuft kein Starlight-Fenster: Starte Starlight selbst neu.",
    "choose a model": "Wähle ein Modell", "the context size is a number": "Die Kontextgröße ist eine Zahl",
    // Starlight's own questions (askYesNo, askText) and the conversation's menu
    "Cancel": "Abbrechen", "Rename": "Umbenennen", "New name": "Neuer Name", "Leave": "Verlassen",
    "Forget": "Vergessen", "Allow": "Erlauben", "Turn on": "Einschalten",
    "Choose": "Wählen", "Leave practice mode?": "Übungsmodus verlassen?",
    "Actions will run for real again.": "Aktionen werden wieder wirklich ausgeführt.",
    "Folder (empty: none)": "Ordner (leer: keiner)", "The conversation stays.": "Das Gespräch bleibt.",
    "Pin to the top": "Oben anheften", "Unpin": "Nicht mehr anheften",
    "Put in a folder": "In einen Ordner legen", "Archive": "Archivieren",
    "Take out of the archive": "Aus dem Archiv holen",
    "Forget what it learned here": "Vergessen, was hier gelernt wurde",
    "Download as Markdown": "Als Markdown herunterladen",
    "Download as a web page": "Als Webseite herunterladen",
    "Print or save as PDF": "Drucken oder als PDF speichern",
    "Save this automation?": "Diese Automation speichern?", "Forget this?": "Das vergessen?",
    "Turn it on?": "Einschalten?", "It can no longer use Starlight.": "Es kann Starlight dann nicht mehr nutzen.",
    // a sign when an answer is ready (chat, settings)
    "Your answer is ready": "Deine Antwort ist fertig",
    "Starlight is waiting for your OK": "Starlight wartet auf dein OK",
    "When an answer is ready": "Wenn eine Antwort fertig ist",
    "Tell me when an answer is ready": "Sag mir Bescheid, wenn eine Antwort fertig ist",
    "Allow notifications": "Benachrichtigungen erlauben",
    "Notifications are allowed in this browser.": "Benachrichtigungen sind in diesem Browser erlaubt.",
    "Notifications are blocked in this browser's settings.": "Benachrichtigungen sind in den Einstellungen dieses Browsers blockiert.",
    "— when Starlight's window is in the background, its title gets a dot, the taskbar button lights up (in Starlight's own window), and the browser shows a notification if you allowed it. Kept for this device.": "— Wenn Starlights Fenster im Hintergrund ist, bekommt sein Titel einen Punkt, die Schaltfläche in der Taskleiste leuchtet (in Starlights eigenem Fenster), und der Browser zeigt eine Benachrichtigung, wenn du sie erlaubt hast. Gilt nur für dieses Gerät.",
    // the chat
    "Starlight is not answering: the PC may be off or asleep.": "Starlight antwortet nicht: Der PC ist vielleicht aus oder im Ruhezustand.",
    "Wake the PC": "PC aufwecken", "connecting…": "verbinde…", "connected": "verbunden", "offline": "offline",
    "you": "du", "Jump to the latest message": "Zur neuesten Nachricht springen",
    "Keyboard shortcuts": "Tastenkürzel", "Press Ctrl+/ for the keyboard shortcuts.": "Drücke Strg+/ für die Tastenkürzel.",
    "Ctrl": "Strg", "Shift": "Umschalt", "Space": "Leertaste",
    "A new line": "Eine neue Zeile", "Stop the answer, or close a menu": "Die Antwort anhalten oder ein Menü schließen",
    "In the empty box: your last question again": "Im leeren Feld: deine letzte Frage noch einmal", "Commands": "Befehle",
    "Kill switch: stop everything Starlight does (anywhere on the PC)": "Not-Aus: alles anhalten, was Starlight tut (überall auf dem PC)",
    "These shortcuts (? when not typing)": "Diese Tastenkürzel (? wenn du nicht schreibst)",
    "These shortcuts (Ctrl+/ while typing in a box)": "Diese Tastenkürzel (Strg+/ beim Schreiben in einem Feld)",
    "A new note": "Eine neue Notiz",
    "Edit or read the note": "Die Notiz bearbeiten oder lesen",
    "Close the side panel or the finder": "Die Seitenleiste oder die Suche schließen",
    "Flashcards: show the answer": "Karteikarten: die Antwort zeigen", "Flashcards: how well you knew it": "Karteikarten: wie gut du es wusstest",
    "New conversation. Ask anything.": "Neues Gespräch. Frag einfach.",
    "Private conversation": "Privates Gespräch",
    "It is never saved and nothing is learned from it. It is gone when Starlight stops.": "Es wird nie gespeichert, und nichts daraus wird gelernt. Es ist weg, sobald Starlight anhält.",
    "Starlight runs on this PC: nothing you say leaves it.": "Starlight läuft auf diesem PC: Nichts, was du sagst, verlässt ihn.",
    "Type / for commands. Drop pictures or documents here to add them.": "Tippe / für Befehle. Zieh Bilder oder Dokumente hierher, um sie anzuhängen.",
    "practice mode · Off": "Übungsmodus · Aus", "Practice": "Übung", "private": "privat",
    "model paused · Resume": "Modell pausiert · Fortsetzen", "Paused ▶": "Pausiert ▶",
    "This PC": "Dieser PC", "Pages": "Seiten", "Model": "Modell", "Context": "Kontext", "Recent": "Zuletzt",
    "Shows after the next answer.": "Erscheint nach der nächsten Antwort.",
    "Older turns are summarized when it is full.": "Ältere Beiträge werden zusammengefasst, wenn er voll ist.",
    "Nearly full: older turns get summarized. /new starts afresh.": "Fast voll: Ältere Beiträge werden zusammengefasst. /new fängt neu an.",
    "The model that answers": "Das Modell, das antwortet",
    "This conversation: rename, pin, folder, archive, download, delete": "Dieses Gespräch: umbenennen, anheften, Ordner, archivieren, herunterladen, löschen",
    "Kill switch": "Not-Aus", "Kill": "Stopp", "Resume": "Fortsetzen", "Send": "Senden", "Stop": "Stopp",
    "Allowed servers": "Erlaubte Server", "none": "keine", "Logins": "Anmeldungen", "Secrets": "Geheimnisse",
    "Saved files": "Gespeicherte Dateien", "Drop pictures or documents to add them to your message": "Bilder oder Dokumente hier ablegen, um sie anzuhängen",
    "Search chats": "Chats durchsuchen", "New conversation": "Neues Gespräch", "New private conversation": "Neues privates Gespräch",
    "New private conversation: never saved, nothing learned from it, gone when Starlight stops": "Neues privates Gespräch: wird nie gespeichert, nichts wird daraus gelernt, weg, wenn Starlight endet",
    "Conversations": "Gespräche",
    "Practice mode: actions that change something are described, not done. Press to turn it off": "Übungsmodus: Aktionen, die etwas ändern, werden beschrieben, nicht ausgeführt. Drücken zum Ausschalten",
    "A private conversation: never saved, nothing is learned from it, and it is gone when Starlight stops": "Ein privates Gespräch: wird nie gespeichert, nichts wird daraus gelernt, und es ist weg, wenn Starlight endet",
    "Game mode: the model is off the graphics card. Press to bring it back now": "Spielmodus: Das Modell ist von der Grafikkarte genommen. Drücken, um es jetzt zurückzuholen",
    "Things Starlight prepared wait for your OK": "Was Starlight vorbereitet hat, wartet auf dein OK",
    "Read answers aloud": "Antworten vorlesen",
    "Kill switch: stop everything Starlight does on this PC, until you resume (also Ctrl+Alt+J)": "Not-Aus: alles anhalten, was Starlight auf diesem PC tut, bis du fortsetzt (auch Strg+Alt+J)",
    "The conversation": "Das Gespräch", "Add a picture or document": "Bild oder Dokument anhängen",
    "Take a photo: read the text in it, translate it or describe it": "Foto aufnehmen: den Text lesen, übersetzen oder beschreiben lassen",
    "Take a photo": "Foto aufnehmen", "Speak (press again to stop)": "Sprechen (nochmal drücken zum Beenden)", "Speak": "Sprechen",
    "Conversation: just talk, and hear the answers; speaking interrupts an answer": "Unterhaltung: einfach sprechen und die Antworten hören; wer spricht, unterbricht die Antwort",
    "Conversation mode": "Unterhaltungsmodus", "Your message": "Deine Nachricht",
    "Ask Starlight…  (Enter to send, Shift+Enter for a new line)": "Frag Starlight…  (Enter zum Senden, Umschalt+Enter für eine neue Zeile)",
    "Ask Starlight…": "Frag Starlight…",
    "Edit": "Bearbeiten", "Copy": "Kopieren", "Copied": "Kopiert", "Regenerate": "Neu antworten", "Branch": "Abzweigen",
    "Change this question and ask it again (its answer is replaced)": "Diese Frage ändern und neu stellen (die Antwort wird ersetzt)",
    "Ask the last message again and replace this answer": "Die letzte Nachricht neu stellen und diese Antwort ersetzen",
    "A new conversation up to here, to go another way (this one stays)": "Ein neues Gespräch bis hierhin, um anders weiterzumachen (dieses bleibt)",
    "Change your question … (Enter asks it again, Esc cancels)": "Ändere deine Frage … (Enter stellt sie neu, Esc bricht ab)",
    "Connection lost: the answer was interrupted.": "Verbindung verloren: Die Antwort wurde unterbrochen.",
    "Thinking …": "Denke nach …", "Kill switch engaged. Press Resume on the PC to continue.": "Not-Aus aktiv. Drücke am PC auf Fortsetzen, um weiterzumachen.",
    "[stopped]": "[angehalten]", "Starlight is still working on the last message.": "Starlight arbeitet noch an der letzten Nachricht.",
    "Approve": "Erlauben", "Deny": "Ablehnen", "Remove": "Entfernen", "Listening…": "Höre zu…",
    "Conversation: just talk (press 🗣 to end)": "Unterhaltung: einfach sprechen (🗣 zum Beenden)",
    "Archived conversations": "Archivierte Gespräche", "Back to your conversations": "Zurück zu deinen Gesprächen",
    "Archived (see Archived conversations in the list)": "Archiviert (siehe Archivierte Gespräche in der Liste)",
    "Rename, pin, folder, archive, download, delete": "Umbenennen, anheften, Ordner, archivieren, herunterladen, löschen",
    "More for this conversation": "Mehr zu diesem Gespräch",
    "Read the text": "Text lesen", "Translate it": "Übersetzen", "Describe it": "Beschreiben",
    "Shared: say what to do with it, then Send": "Geteilt: Sag, was damit passieren soll, dann Senden",
    "Press the talk button, next to the microphone, to start": "Drücke den Gesprächsknopf neben dem Mikrofon, um zu beginnen",
    "Could not speak": "Konnte nicht sprechen", "Could not understand that": "Das habe ich nicht verstanden",
    "Starlight is still answering: Esc or Stop ends it": "Starlight antwortet noch: Esc oder Stopp beendet das",
    "Stopping …": "Halte an …", "The microphone needs https:// or this PC": "Das Mikrofon braucht https:// oder diesen PC",
    // the activity page and the approval queue
    "Waiting for your OK": "Wartet auf dein OK",
    "Starlight prepared these: a reply it drafted, a step an automation was not allowed to take by itself.": "Das hat Starlight vorbereitet: eine entworfene Antwort, ein Schritt, den eine Automation nicht allein tun durfte.",
    "runs exactly what is shown;": "führt genau das Gezeigte aus;", "Dismiss": "Verwerfen", "drops it.": "verwirft es.",
    "What Starlight did": "Was Starlight getan hat",
    "Every action it took or was refused, newest first, from": "Jede Aktion, ausgeführt oder abgelehnt, die neueste zuerst, aus",
    ". Passwords and keys are hidden there.": ". Passwörter und Schlüssel sind dort verborgen.",
    "Search, e.g. mail.send": "Suchen, z. B. mail.send", "Search the activity": "Aktivität durchsuchen", "Nothing yet.": "Noch nichts.",
    "All": "Alles", "Actions": "Aktionen", "Refused": "Abgelehnt", "Failed": "Fehlgeschlagen", "Answers": "Antworten",
    "action": "Aktion", "refused": "abgelehnt", "failed": "fehlgeschlagen", "practice": "Übung", "kill switch": "Not-Aus", "setting": "Einstellung", "answer": "Antwort",
    "Dismissed": "Verworfen",
    // automations
    "Your automations": "Deine Automationen",
    "Rules that run on a schedule or when something happens. They can be changed on the PC only.": "Regeln, die nach Zeitplan oder bei einem Ereignis laufen. Sie lassen sich nur am PC ändern.",
    "New automation": "Neue Automation", "Name": "Name", "When": "Wann", "On a schedule": "Nach Zeitplan",
    "When something happens": "Wenn etwas passiert", "If (optional)": "Wenn (optional)", "always": "immer",
    "is": "ist", "is not": "ist nicht", "contains": "enthält", "is more than": "ist mehr als", "is less than": "ist weniger als",
    "is at least": "ist mindestens", "is at most": "ist höchstens", "Then": "Dann", "+ Add a step": "+ Schritt hinzufügen",
    "In a step,": "In einem Schritt ist", "or": "oder", "is what the step before gave back, and": "das, was der Schritt davor zurückgab, und",
    "a field of the event. Tools that normally ask first are allowed for this rule when you save it.": "ein Feld des Ereignisses. Werkzeuge, die sonst erst fragen, werden beim Speichern für diese Regel erlaubt.",
    "Check": "Prüfen", "Save": "Speichern", "Clear": "Leeren", "Ready-made": "Vorlagen", "Use": "Verwenden",
    "fills the form above; change what you like, then save.": "füllt das Formular oben aus; ändere, was du möchtest, dann speichern.",
    "e.g. usb-backup (lowercase, - or _)": "z. B. usb-backup (Kleinbuchstaben, - oder _)", "Schedule": "Zeitplan",
    "weekdays 7:30 · every 2 hours · monday 9am · every month on the 1st at 10:00": "weekdays 7:30 · every 2 hours · monday 9am · every month on the 1st at 10:00 (englisch)",
    "Event": "Ereignis", "Field": "Feld", "Comparison": "Vergleich", "Value": "Wert", "value, e.g. nas": "Wert, z. B. nas",
    "No condition: it runs each time the event happens.": "Keine Bedingung: Es läuft jedes Mal, wenn das Ereignis eintritt.",
    "How the conditions go together": "Wie die Bedingungen zusammengehören", "All of these": "Alle davon",
    "Any of these": "Eine davon", "+ Add a condition": "+ Bedingung hinzufügen", "Remove this condition": "Diese Bedingung entfernen",
    "Write it out": "Ausschreiben", "Use the form": "Formular nutzen", "The condition, written out": "Die Bedingung, ausgeschrieben",
    "e.g. server is nas or (server is router and status is down)": "z. B. server ist nas oder (server ist router und status ist down)",
    "This condition can only be written out.": "Diese Bedingung lässt sich nur ausgeschrieben bearbeiten.",
    'Written out: and, or, not and brackets; is, is not, has, <, >. Words with "and" or "or" in them go in quotes.':
      "Ausgeschrieben: and, or, not und Klammern (auch und, oder, nicht); is, is not, has, <, >. Wörter mit „and“ oder „or“ darin kommen in Anführungszeichen.",
    "Kind of step": "Art des Schritts", "Run a tool": "Werkzeug ausführen", "Ask Starlight": "Starlight fragen", "Tell me": "Mir sagen",
    "tool, e.g. briefing.send": "Werkzeug, z. B. briefing.send", "Arguments": "Argumente", "Text": "Text", "Only if": "Nur wenn",
    "what to ask, or the message": "was gefragt werden soll, oder die Nachricht",
    "only if the step before gave, e.g. count > 0 (optional)": "nur wenn der Schritt davor das ergab, z. B. count > 0 (optional)",
    'arguments as JSON, e.g. {"days": 1}': 'Argumente als JSON, z. B. {"days": 1}',
    "Remove this step": "Diesen Schritt entfernen", "Run now": "Jetzt ausführen", "Switch off": "Ausschalten", "Switch on": "Einschalten",
    "needs a new schedule": "braucht einen neuen Zeitplan",
    "Delete": "Löschen", "on": "an", "off": "aus",
    "None yet. Make one below, or start from a ready-made one.": "Noch keine. Leg unten eine an oder beginne mit einer Vorlage.",
    // the kill switch page
    "STOP": "STOPP", "everything": "alles",
    "One press stops everything Starlight is doing on the PC, until you resume there.": "Ein Druck hält alles an, was Starlight auf dem PC tut, bis du dort fortsetzt.",
    "Add this page to your home screen for a one-tap kill switch.": "Leg diese Seite auf den Startbildschirm: ein Tipp, und alles hält an.",
    "Back to the chat": "Zurück zum Chat", "Stopped": "Angehalten",
    "Starlight is stopped. Press Resume on the PC to let it go on.": "Starlight ist angehalten. Drücke am PC auf Fortsetzen, damit es weitergeht.",
    // memory
    "Memory & skills": "Gedächtnis & Fähigkeiten", "What Starlight remembers about you": "Was Starlight über dich weiß",
    "Each memory is a plain text file in the": "Jede Erinnerung ist eine einfache Textdatei im Ordner", "memory": "memory",
    "folder. Edit or delete anything that is wrong or that you don't want kept.": ". Bearbeite oder lösche, was falsch ist oder nicht bleiben soll.",
    "Skills it learned": "Gelernte Fähigkeiten", "Drafts are only used after you approve them": "Entwürfe werden erst genutzt, wenn du sie erlaubst",
    "here": "hier", '. Every change is kept, so "Undo" brings back the version before.': '. Jede Änderung wird aufbewahrt, „Rückgängig" holt die Fassung davor zurück.',
    "⤒ Import a skill": "⤒ Fähigkeit importieren",
    "A file someone exported: it comes in as a draft for you to read and approve.": "Eine Datei, die jemand exportiert hat: Sie kommt als Entwurf, den du liest und erlaubst.",
    "Search memories": "Erinnerungen durchsuchen", "Export": "Exportieren",
    "No skills yet. Starlight drafts one when the same kind of task works twice.": "Noch keine Fähigkeiten. Starlight entwirft eine, wenn dieselbe Art Aufgabe zweimal klappt.",
    "Timeline": "Zeitleiste", "People & places": "Personen & Orte", "Undo": "Rückgängig",
    // pairing
    "This device isn't paired yet. On your PC, open Starlight's": "Dieses Gerät ist noch nicht gekoppelt. Öffne am PC Starlights",
    "and press": "und drücke", "Show a pairing code": "Kopplungscode zeigen", "(or run": "(oder führe", "), then enter the code here.": "aus) und gib den Code hier ein.",
    "Pair": "Koppeln", "Pairing code": "Kopplungscode", "Name for this device (e.g. My phone)": "Name für dieses Gerät (z. B. Mein Handy)",
    "Name for this device": "Name für dieses Gerät", "Paired. Opening the chat…": "Gekoppelt. Der Chat öffnet sich…",
    // settings
    "Viewing from another device.": "Ansicht von einem anderen Gerät.",
    "Permissions, modules, devices and skills can only be changed on the PC itself.": "Berechtigungen, Module, Geräte und Fähigkeiten lassen sich nur am PC selbst ändern.",
    "Your phone and other devices": "Dein Handy und andere Geräte", "Device": "Gerät", "Paired": "Gekoppelt", "Last used": "Zuletzt benutzt",
    "Voice": "Stimme",
    "Which voice reads answers aloud, for each language. More can be installed here (German and English, from Piper's voices; 20 to 120 MB each). Everything runs on this PC.": "Welche Stimme Antworten vorliest, je Sprache. Weitere lassen sich hier installieren (Deutsch und Englisch, aus Pipers Stimmen; je 20 bis 120 MB). Alles läuft auf diesem PC.",
    "Install another voice": "Weitere Stimme installieren", "German": "Deutsch", "English": "Englisch",
    "— listen on this PC's microphone for the phrase, then answer out loud. Only short bits of speech are written down, on this PC, to look for it.": "— am Mikrofon dieses PCs auf den Satz hören und dann laut antworten. Nur kurze Sprachstücke werden dafür mitgeschrieben, auf diesem PC.",
    "In plain words": "In einfachen Worten", "What Starlight may do right now. A whole module is shown as": "Was Starlight gerade darf. Ein ganzes Modul erscheint als",
    "; open it below to see each tool.": "; unten aufklappen, um jedes Werkzeug zu sehen.", "Practice mode": "Übungsmodus",
    "— actions that change something (sending mail, clicking, running code, commands …) are described, not done. Looking things up still works.": "— Aktionen, die etwas ändern (Mail senden, klicken, Code ausführen, Befehle …), werden beschrieben, nicht ausgeführt. Nachschlagen geht weiter.",
    "Modules and what they may do": "Module und was sie dürfen",
    "Turn a module off and its tools are gone. Open one to choose, tool by tool, whether it": "Schalte ein Modul aus, und seine Werkzeuge sind weg. Klapp eines auf, um je Werkzeug zu wählen, ob es",
    "runs": "läuft", "on its own,": "von selbst,", "asks first": "erst fragt", ", or is": " oder", "blocked": "gesperrt ist",
    ". Changes are saved in config/permissions.chat.yaml.": ". Änderungen landen in config/permissions.chat.yaml.",
    "Advanced: one rule for several tools": "Erweitert: eine Regel für mehrere Werkzeuge", "A pattern such as": "Ein Muster wie",
    '. "Default" removes the rule again.': '. „Standard" entfernt die Regel wieder.',
    // the Notes page (notes.html)
    "Notes": "Notizen", "Starlight notes": "Starlight-Notizen", "Skip to the note": "Zur Notiz springen",
    "Find a note (Ctrl+K)": "Notiz finden (Strg+K)", "Your notes": "Deine Notizen", "Search notes": "Notizen durchsuchen",
    "Views": "Ansichten", "Today": "Heute", "Inbox": "Eingang", "Tasks": "Aufgaben", "Graph": "Netz",
    "Pinned": "Angeheftet", "Folders": "Ordner", "About this note": "Zu dieser Notiz", "Outline": "Gliederung",
    "Linked from": "Verlinkt von", "Properties": "Eigenschaften", "Find a note": "Notiz finden",
    "Type a note's name": "Namen einer Notiz tippen", "↑↓ choose · Enter opens · Esc closes": "↑↓ wählen · Enter öffnet · Esc schließt",
    "Only the first 20,000 notes are listed": "Nur die ersten 20.000 Notizen werden gezeigt",
    "Starlight folder": "Starlight-Ordner", "No notes here yet": "Noch keine Notizen hier",
    "There is no note called": "Es gibt keine Notiz namens", "★ Pinned": "★ Angeheftet", "☆ Pin": "☆ Anheften",
    "Unpin this note": "Nicht mehr anheften", "Pin this note to the top of the list": "Diese Notiz oben in der Liste anheften",
    "This note's properties could not be read:": "Die Eigenschaften dieser Notiz ließen sich nicht lesen:",
    "This note is empty.": "Diese Notiz ist leer.", "No headings": "Keine Überschriften",
    "No other note links here yet": "Noch keine andere Notiz verlinkt hierher", "None": "Keine",
    "1 note": "1 Notiz", "notes": "Notizen", "Nothing found. Try fewer or other words.": "Nichts gefunden. Versuch es mit weniger oder anderen Wörtern.",
    "No notes yet": "Noch keine Notizen",
    "Ask Starlight in the chat to write one, for example: “Make a note: buy milk”.":
      "Bitte Starlight im Chat, eine zu schreiben, zum Beispiel: „Notiere: Milch kaufen“.",
    "Choose a note on the left, or press Ctrl+K to find one.": "Wähle links eine Notiz oder drück Strg+K, um eine zu finden.",
    "Opened lately": "Zuletzt geöffnet",
    "No journal entry for today yet. Tell Starlight in the chat: “Write in my journal: …”.":
      "Noch kein Tagebucheintrag für heute. Sag Starlight im Chat: „Schreib in mein Tagebuch: …“.",
    "The inbox is empty. Notes written without a title land here; clipped pages go to Clippings.":
      "Der Eingang ist leer. Notizen ohne Titel landen hier; gespeicherte Webseiten unter Clippings.",
    "Notes written without a title wait here. Starlight can file them into your notes; it asks before moving anything.":
      "Notizen ohne Titel warten hier. Starlight kann sie in deine Notizen einsortieren; es fragt, bevor es etwas verschiebt.",
    "Sort my inbox": "Eingang sortieren",
    "Search tips:": "Suchtipps:",
    "Show the tasks as": "Aufgaben zeigen als", "Columns": "Spalten", "Open": "Offen",
    "Doing": "In Arbeit", "Done": "Erledigt", "Move to": "Verschieben nach", "Nothing here": "Hier ist nichts",
    "Copy a link to this note (it opens the note from other programs)":
      "Link zu dieser Notiz kopieren (er öffnet die Notiz aus anderen Programmen)",
    "Link copied:": "Link kopiert:", "The link to this note:": "Der Link zu dieser Notiz:",
    "Drawing the diagram…": "Das Diagramm wird gezeichnet…", "Show the code": "Code zeigen",
    "Diagram": "Diagramm", "This diagram could not be drawn:": "Dieses Diagramm ließ sich nicht zeichnen:",
    "it is too long": "es ist zu lang",
    "Live preview": "Live-Vorschau",
    "Live preview: the marks show only on the line you are on (click for all)":
      "Live-Vorschau: Die Zeichen erscheinen nur in der Zeile, in der du bist (Klick zeigt alle)",
    "All the Markdown marks show (click for live preview)": "Alle Markdown-Zeichen sind zu sehen (Klick für die Live-Vorschau)",
    "Flashcards": "Lernkarten", "Study all": "Alle lernen", "Study": "Lernen", "All decks": "Alle Stapel",
    "Show answer": "Antwort zeigen", "Again": "Nochmal", "Hard": "Schwer", "Good": "Gut", "Easy": "Leicht",
    "today": "heute", "in 1 day": "in 1 Tag", "in 1 month": "in 1 Monat", "in 1 year": "in 1 Jahr",
    "Back to the decks": "Zurück zu den Stapeln", "Done for now.": "Für jetzt geschafft.",
    "Space: show the answer · 1 to 4: grade it": "Leertaste: Antwort zeigen · 1 bis 4: bewerten",
    "No flashcards yet. Write lines like this in a note tagged #flashcards:":
      "Noch keine Lernkarten. Schreib Zeilen wie diese in eine Notiz mit dem Schlagwort #lernkarten (oder #flashcards):",
    "Question:::Answer asks both ways; #flashcards/spanish puts the cards in a deck called spanish.":
      "Frage:::Antwort fragt in beide Richtungen; #lernkarten/spanisch legt die Karten in einen Stapel namens spanisch.",
    "Export: HTML, PDF or print": "Exportieren: HTML, PDF oder drucken", "HTML file": "HTML-Datei",
    "PDF file": "PDF-Datei", "Print": "Drucken",
    "Boards": "Boards", "Board": "Board", "＋ New board": "＋ Neues Board",
    "What should the board be called?": "Wie soll das Board heißen?",
    "No boards yet. A board holds cards and arrows: ideas, notes, links, side by side.":
      "Noch keine Boards. Ein Board hält Karten und Pfeile: Ideen, Notizen, Links nebeneinander.",
    "The board is not saved yet. Try again in a moment.": "Das Board ist noch nicht gespeichert. Versuch es gleich noch einmal.",
    "Load it again": "Neu laden", "Group": "Gruppe", "Web link": "Weblink",
    "Add a text card": "Textkarte hinzufügen", "Add a note card": "Notizkarte hinzufügen",
    "Add a web link": "Weblink hinzufügen", "Add a group": "Gruppe hinzufügen",
    "Which note? (its name)": "Welche Notiz? (ihr Name)",
    "The web address (https://…)": "Die Webadresse (https://…)",
    "A link card needs a web address that starts with https://": "Eine Linkkarte braucht eine Webadresse, die mit https:// beginnt",
    "Drag to another card to draw an arrow": "Auf eine andere Karte ziehen, um einen Pfeil zu zeichnen",
    "Drag to resize": "Ziehen, um die Größe zu ändern", "Card text": "Kartentext",
    "This note is not there any more.": "Diese Notiz gibt es nicht mehr.",
    "No links yet": "Noch keine Links", "The notes linked with this one": "Die mit dieser Notiz verlinkten Notizen",
    "Note": "Notiz", "Sort by this column": "Nach dieser Spalte sortieren", "Look again": "Neu suchen",
    "This table could not be made:": "Diese Tabelle ließ sich nicht erstellen:",
    "Related": "Verwandt", "Mentioned without a link": "Ohne Link erwähnt", "Link": "Verlinken",
    "Make it a link to this note": "Zu einem Link auf diese Notiz machen", "Linked": "Verlinkt",
    "No related notes yet": "Noch keine verwandten Notizen",
    "No mentions without a link": "Keine Erwähnungen ohne Link",
    "links here": "verlinkt hierher", "linked from here": "von hier verlinkt", "similar": "ähnlich",
    // the Notes page: tasks
    "Overdue": "Überfällig", "Upcoming": "Demnächst", "No date": "Ohne Datum",
    "Tick it off": "Abhaken", "Open it again": "Wieder öffnen",
    "No open tasks. Write one in any note, for example: - [ ] Call Tom due: friday":
      "Keine offenen Aufgaben. Schreib eine in eine beliebige Notiz, zum Beispiel: - [ ] Tom anrufen fällig: Freitag",
    "Due": "Fällig", "Starts": "Beginnt", "Done on": "Erledigt am", "Repeats": "Wiederholt sich",
    "High priority": "Hohe Priorität", "Low priority": "Niedrige Priorität",
    "A map of how your notes link together": "Eine Karte, wie deine Notizen zusammenhängen",
    "All folders": "Alle Ordner", "All tags": "Alle Schlagwörter", "Memories": "Erinnerungen",
    "Folder": "Ordner", "Tag": "Schlagwort", "note": "Notiz", "links": "Links",
    "only the ones with the most links": "nur die mit den meisten Links",
    "No notes to show.": "Keine Notizen zu zeigen.", "Zoom in": "Vergrößern", "Zoom out": "Verkleinern",
    "Fit": "Einpassen",
    "No note with that name": "Keine Notiz mit diesem Namen",
    // the Notes page: editing
    "New note (Ctrl+N)": "Neue Notiz (Strg+N)", "Trash": "Papierkorb",
    "This note was changed somewhere else": "Diese Notiz wurde woanders geändert",
    "Another program or tab changed it after you opened it. Nothing is lost: choose what to keep.":
      "Ein anderes Programm oder ein anderer Tab hat sie geändert, nachdem du sie geöffnet hast. Nichts geht verloren: Wähle, was bleiben soll.",
    "Show the other version": "Die andere Fassung zeigen", "Keep mine": "Meine behalten",
    "Take the other version": "Die andere Fassung nehmen", "Keep both": "Beide behalten",
    "Earlier versions": "Frühere Fassungen",
    "Starlight keeps a version before each change. Choose one to see what changed since then.":
      "Starlight hebt vor jeder Änderung eine Fassung auf. Wähle eine, um zu sehen, was sich seitdem geändert hat.",
    "Put this version back": "Diese Fassung zurückholen", "Close": "Schließen",
    "✎ Edit": "✎ Bearbeiten", "✓ Done": "✓ Fertig", "Edit or read this note (Ctrl+E)": "Notiz bearbeiten oder lesen (Strg+E)",
    "Move to the trash": "In den Papierkorb", "Bold (Ctrl+B)": "Fett (Strg+B)", "Italic (Ctrl+I)": "Kursiv (Strg+I)",
    "Heading": "Überschrift", "Subheading": "Unterüberschrift", "Bullet list": "Liste",
    "Task (Ctrl+Enter ticks it)": "Aufgabe (Strg+Enter hakt sie ab)", "Task": "Aufgabe",
    "Link to a note": "Link zu einer Notiz", "Picture": "Bild", "Table": "Tabelle", "Callout": "Hinweisbox",
    "Today's date": "Heutiges Datum", "Template": "Vorlage", "Templates come in a later update.": "Vorlagen kommen mit einem späteren Update.",
    "Title": "Titel", "Formatting": "Formatierung",
    "Write here. Type / for headings, tasks, tables and more.": "Hier schreiben. Tipp / für Überschriften, Aufgaben, Tabellen und mehr.",
    "Editing…": "Bearbeitet…", "Saving…": "Speichert…", "Not saved: changed elsewhere": "Nicht gespeichert: woanders geändert",
    "Not saved:": "Nicht gespeichert:",
    "Not saved yet: your text is still here. Try again in a moment.": "Noch nicht gespeichert: Dein Text ist noch da. Versuch es gleich noch einmal.",
    "Untitled": "Ohne Titel",
    "Move this note to the trash?": "Diese Notiz in den Papierkorb legen?",
    "You can bring it back from the trash.": "Du kannst sie aus dem Papierkorb zurückholen.",
    "Change the links": "Links ändern", "Leave the links": "Links lassen", "Create": "Anlegen",
    "Moved to the trash": "In den Papierkorb gelegt",
    "The trash is empty. Deleted notes wait here until you bring them back.":
      "Der Papierkorb ist leer. Gelöschte Notizen warten hier, bis du sie zurückholst.",
    "Deleted notes. Bring one back as it was.": "Gelöschte Notizen. Hol eine zurück, wie sie war.",
    "Bring back": "Zurückholen", "It is back": "Sie ist wieder da",
    "changed here": "hier geändert", "before renaming": "vor dem Umbenennen", "before deleting": "vor dem Löschen",
    "before putting a version back": "vor dem Zurückholen einer Fassung", "before Starlight added to it": "bevor Starlight etwas ergänzte",
    "before Starlight rewrote it": "bevor Starlight sie neu schrieb", "before a journal entry": "vor einem Tagebucheintrag",
    "before the weekly review": "vor dem Wochenrückblick", "before Starlight changed it": "bevor Starlight sie änderte",
    "before an undo": "vor einem Rückgängigmachen",
    "Choose a version on the left to see what changed since then.": "Wähle links eine Fassung, um zu sehen, was sich seitdem geändert hat.",
    "No earlier versions yet. Starlight keeps one before each change.": "Noch keine früheren Fassungen. Starlight hebt vor jeder Änderung eine auf.",
    "No differences: the note is the same as then.": "Keine Unterschiede: Die Notiz ist wie damals.",
    "Green: added since then. Red: there then, gone now.": "Grün: seitdem dazugekommen. Rot: damals da, jetzt weg.",
    "The earlier version is back. The text before it was kept too.": "Die frühere Fassung ist zurück. Der Text davor wurde auch aufgehoben.",
    // the Notes page: links between notes
    "Not written yet: click to write it": "Noch nicht geschrieben: Klick, um sie zu schreiben",
    "There is no note with this name yet.": "Es gibt noch keine Notiz mit diesem Namen.", "Write it": "Schreiben",
    "(shown above:": "(oben schon gezeigt:",
    "Also change the links to this note in other notes? Notes that change:": "Die Links auf diese Notiz in anderen Notizen mitändern? Betroffene Notizen:",
    "Each keeps its earlier version.": "Jede behält ihre frühere Fassung.",
    "Renamed. Links changed in other notes:": "Umbenannt. Links geändert in anderen Notizen:",
    // the Notes page: tags and properties
    "Tags": "Schlagwörter", "Finish editing first (Done), then change properties here.": "Erst fertig bearbeiten (Fertig), dann die Eigenschaften hier ändern.",
    "Remove this property": "Diese Eigenschaft entfernen", "＋ Add a property": "＋ Eigenschaft hinzufügen",
    "Kind": "Art", "Add": "Hinzufügen", "Number": "Zahl",
    "Date": "Datum", "List": "Liste", "Yes or no": "Ja oder nein",
    "Give the property a name and a value.": "Gib der Eigenschaft einen Namen und einen Wert.",
    "yes": "ja", "no": "nein",
    // "Ask Starlight" on a note, and the chat that opens with the request
    "✨ Ask": "✨ Fragen", "Ask Starlight about this note": "Starlight zu dieser Notiz fragen",
    "Check the request, then press Send": "Prüf die Anfrage, dann drück Senden",
    // templates, the journal's day page and calendar strip
    "Empty note": "Leere Notiz", "From a template": "Aus einer Vorlage", "No journal page for today yet.": "Noch keine Tagebuchseite für heute.",
    "Write today's page": "Heutige Seite schreiben", "Journal days": "Tagebuchtage", "no page yet": "noch keine Seite",
    // voice memos on the Notes page
    "Voice memo: speak, it is kept as text": "Sprachnotiz: sprechen, sie wird als Text aufgehoben",
    "Recording… press the button again to stop": "Nimmt auf… zum Beenden nochmal drücken", "Nothing was understood": "Nichts verstanden",
    "Memo kept in the inbox": "Notiz im Eingang aufgehoben", "Memo kept in the journal": "Notiz im Tagebuch aufgehoben",
    // the chat: a file the assistant changed
    "Changed:": "Geändert:", "New:": "Neu:", "Undone:": "Rückgängig gemacht:",
    // settings: safety limits (the switches' own texts come from the server in German)
    "Safety limits": "Sicherheitsgrenzen", "Safety level": "Sicherheitsstufe",
    "How careful Starlight is. Pick a level, then change any single protection. Saved in config/starlight.safety.yaml.":
      "Wie vorsichtig Starlight ist. Wähle eine Stufe und ändere dann einzelne Schutzmaßnahmen. Gespeichert in config/starlight.safety.yaml.",
    "Strict": "Streng", "Balanced": "Ausgewogen", "Relaxed": "Locker",
    "Strict: everything that changes something asks first, tighter limits, and other devices ask before every action.":
      "Streng: Alles, was etwas ändert, fragt vorher, engere Grenzen, und andere Geräte fragen vor jeder Aktion.",
    "Balanced: Starlight as it came, as your settings files say.":
      "Ausgewogen: Starlight wie geliefert, so wie es in deinen Einstellungsdateien steht.",
    "Relaxed: fewer questions. No second question after reading web pages or mail, no limits, your files anywhere without asking, and the code sandbox may go online.":
      "Locker: weniger Fragen. Keine zweite Frage nach Webseiten oder Mails, keine Grenzen, deine Dateien überall ohne Nachfrage, und die Code-Sandbox darf online gehen.",
    "1 protection is set differently below.": "1 Schutzmaßnahme ist unten anders eingestellt.",
    "config/starlight.local.yaml exists: a setting in it wins over this page.":
      "config/starlight.local.yaml gibt es: Eine Einstellung darin hat Vorrang vor dieser Seite.",
    "Always on:": "Immer an:",
    "the kill switch (Ctrl+Alt+J, the Kill button), passwords and keys masked in the logs, the activity log, and that these limits change only at this PC.":
      "der Not-Aus (Strg+Alt+J, der Stopp-Knopf), Passwörter und Schlüssel in den Protokollen unkenntlich, das Aktivitätsprotokoll, und dass sich diese Grenzen nur an diesem PC ändern lassen.",
    "changed": "geändert", "after a restart": "nach einem Neustart", "seconds": "Sekunden", "minutes": "Minuten",
    "Back to the level's setting": "Zurück zur Einstellung der Stufe",
    "Switch this off?": "Das ausschalten?",
    "Let these pages use Starlight as you?": "Diesen Seiten erlauben, Starlight als du zu nutzen?",
    "The protections you set differently go back to this level.": "Die Schutzmaßnahmen, die du anders eingestellt hast, gehen auf diese Stufe zurück.",
    "Choose this level?": "Diese Stufe wählen?",
    "none — set them under Safety limits below": "keine – unter Sicherheitsgrenzen weiter unten einstellen",
    "Asks first": "Fragt erst", "Runs": "Läuft", "Blocked": "Gesperrt", "Default": "Standard", "Apply": "Anwenden",
    "Find a module or tool": "Modul oder Werkzeug suchen", "Find a module or tool, e.g. desktop, mail or save": "Modul oder Werkzeug suchen, z. B. desktop, mail oder save",
    "e.g. network.* or desktop.click": "z. B. network.* oder desktop.click", "Pattern": "Muster", "Setting": "Einstellung",
    "Nothing matches.": "Nichts passt.", "not from Starlight": "nicht von Starlight", "It has no tools.": "Es hat keine Werkzeuge.",
    "Reset": "Zurücksetzen", "works once, for 5 minutes": "gilt einmal, 5 Minuten lang", "Saved": "Gespeichert",
    "No voice is installed yet: start Starlight.exe with --voice, or install one below.": "Noch keine Stimme installiert: Starte Starlight.exe mit --voice oder installiere unten eine.",
    "▶ Try": "▶ Probehören", "Install": "Installieren", "Downloading…": "Lädt herunter…",
    "Language": "Sprache", "Automatic (like the browser)": "Automatisch (wie der Browser)",
    // backup and restore (settings)
    "Backup and restore": "Sicherung und Wiederherstellung",
    "Saves your conversations, notes, memory, skills and settings into one file. Passwords and paired devices are not included: you enter them or pair again.": "Sichert deine Gespräche, Notizen, Erinnerungen, Fähigkeiten und Einstellungen in einer Datei. Passwörter und gekoppelte Geräte sind nicht enthalten: Du gibst sie neu ein oder koppelst neu.",
    "A restore replaces what you have now, it does not merge. The file is not encrypted: keep it somewhere safe.": "Eine Wiederherstellung ersetzt, was du jetzt hast, sie mischt nichts. Die Datei ist nicht verschlüsselt: Bewahre sie sicher auf.",
    "Save a backup": "Sicherung speichern",
    "Restore from a file…": "Aus einer Datei wiederherstellen…",
    "Backup file": "Sicherungsdatei",
    "Checking the file…": "Datei wird geprüft…",
    "Replace your current conversations, notes, memory and settings with this backup?": "Deine jetzigen Gespräche, Notizen, Erinnerungen und Einstellungen durch diese Sicherung ersetzen?",
    "A copy of what you have now is saved first. It is applied when Starlight starts next.": "Eine Kopie von dem, was du jetzt hast, wird vorher gesichert. Sie wird beim nächsten Start von Starlight angewendet.",
    "Restore": "Wiederherstellen",
    "Ready. Close Starlight and start it again to finish the restore.": "Bereit. Schließe Starlight und starte es neu, um die Wiederherstellung abzuschließen.",
    "Cancel the restore": "Wiederherstellung abbrechen",
    "The restore was cancelled.": "Die Wiederherstellung wurde abgebrochen.",
  };
  const PATTERNS = [
    [/^Delete "(.+)"\?$/, "„$1“ löschen?"],
    [/^Forget the memories Starlight learned in "(.+)"\?$/, "Die Erinnerungen vergessen, die Starlight in „$1“ gelernt hat?"],
    [/^Folder: (.+)$/, "Ordner: $1"],
    [/^context (\d+)$/, "Kontext $1"],
    [/^(.+) does not answer, so there is no list of models\.$/, "$1 antwortet nicht, darum gibt es keine Modellliste."],
    [/^(.+) lists no models\.$/, "$1 meldet keine Modelle."],
    [/^Delete the automation '(.+)'\?$/, "Die Automation „$1“ löschen?"],
    [/^It may run (.+) without asking each time\.$/, "Sie darf $1 ausführen, ohne jedes Mal zu fragen."],
    [/^Delete the skill (.+)\?$/, "Die Fähigkeit $1 löschen?"],
    [/^Let (.+) run without asking you first\?$/, "$1 ausführen lassen, ohne dich vorher zu fragen?"],
    [/^Remove (.+)\?$/, "$1 entfernen?"],
    [/^(\d+) waiting for your OK$/, "$1 warten auf dein OK"],
    [/^(\d+)% used$/, "$1 % belegt"],
    [/^([\d.,]+k?) of ([\d.,]+k?)$/, "$1 von $2"],
    [/^Context (\d+)%$/, "Kontext $1 %"],
    [/^(\d+) shown$/, "$1 angezeigt"],
    [/^works once, for (\d+) minutes$/, "gilt einmal, $1 Minuten lang"],
    [/^Done: (.+)$/, "Erledigt: $1"],
    [/^It did not work: (.+)$/, "Hat nicht geklappt: $1"],
    [/^Approving runs (.+)$/, "Erlauben führt aus: $1"],
    [/^Last try did not work: (.+)$/, "Der letzte Versuch hat nicht geklappt: $1"],
    [/^Runs without asking: (.+)$/, "Läuft ohne Nachfrage: $1"],
    [/^next (.+)$/, "nächstes Mal $1"],
    [/^Saved: (.+)$/, "Gespeichert: $1"],
    [/^This automation: (.+)$/, "Diese Automation: $1"],
    [/^model: (.+)$/, "Modell: $1"],
    [/^Theme: like your system \(click to change\)$/, "Farben: wie das System (Klick wechselt)"],
    [/^Theme: light \(click to change\)$/, "Farben: hell (Klick wechselt)"],
    [/^Theme: dark \(click to change\)$/, "Farben: dunkel (Klick wechselt)"],
    [/^No microphone access: (.+)$/, "Kein Zugriff aufs Mikrofon: $1"],
    [/^Could not reach Starlight: (.+)$/, "Starlight nicht erreichbar: $1"],
    [/^Installed (.+) \((\d+) MB\)$/, "$1 installiert ($2 MB)"],
    [/^(\d+) protections are set differently below\.$/, "$1 Schutzmaßnahmen sind unten anders eingestellt."],
    [/^([a-z0-9_]+) is off$/, "$1 ist aus"],
    // the modules on the Settings page
    [/^1 tool$/, "1 Werkzeug"],
    [/^(\d+) tools$/, "$1 Werkzeuge"],
    [/^1 ask first$/, "1 fragt erst"],
    [/^(\d+) ask first$/, "$1 fragen erst"],
    [/^(\d+) blocked$/, "$1 gesperrt"],
    [/^(\d+) of (\d+) modules on · (\d+) tools: (\d+) ask first, (\d+) blocked, the rest run on their own$/,
     "$1 von $2 Modulen an · $3 Werkzeuge: $4 mit Nachfrage, $5 gesperrt, der Rest läuft von selbst"],
    [/^(\d+) of (\d+) modules match$/, "$1 von $2 Modulen passen"],
    [/^in (\d+) days$/, "in $1 Tagen"],
    [/^in (\d+) months$/, "in $1 Monaten"],
    [/^in (\d+) years$/, "in $1 Jahren"],
    [/^Today: (\d+) due, (\d+) new$/, "Heute: $1 fällig, $2 neu"],
    [/^1 card · (\d+) due · (\d+) new$/, "1 Karte · $1 fällig · $2 neu"],
    [/^(\d+) cards · (\d+) due · (\d+) new$/, "$1 Karten · $2 fällig · $3 neu"],
    [/^(\d+) of (\d+)$/, "$1 von $2"],
    [/^(\d+) known, (\d+) again$/, "$1 gewusst, $2-mal nochmal"],
    [/^(\d+) known$/, "$1 gewusst"],
    [/^Saved\. Not in use yet for (.+): its settings file has a mistake \(see config\/modules\); it applies once that module loads again\.$/,
      "Gespeichert. Für $1 noch nicht in Gebrauch: Seine Einstellungsdatei hat einen Fehler (siehe config/modules); es gilt, sobald das Modul wieder lädt."],
    [/^Backup from (.+), Starlight (.+)\. Conversations: (\d+) · notes: (\d+) · memories: (\d+)$/,
      "Sicherung vom $1, Starlight $2. Gespräche: $3 · Notizen: $4 · Erinnerungen: $5"],
    [/^Restored from the backup of (.+)\. What you had before is in (backups\/.+)\.$/,
      "Wiederhergestellt aus der Sicherung vom $1. Was du vorher hattest, liegt in $2."],
    [/^The restore was not applied: (.+)$/, "Die Wiederherstellung wurde nicht angewendet: $1"],
    [/^1 file over (.+) is not in the backup: (.+)$/,
      "1 Datei über $1 ist nicht in der Sicherung: $2"],
    [/^(\d+) files over (.+) are not in the backup: (.+)$/,
      "$1 Dateien über $2 sind nicht in der Sicherung: $3"],
    [/^1 file with a name that Windows cannot hold is not in the backup: (.+)$/,
      "1 Datei mit einem Namen, den Windows nicht speichern kann, ist nicht in der Sicherung: $1"],
    [/^(\d+) files with a name that Windows cannot hold are not in the backup: (.+)$/,
      "$1 Dateien mit einem Namen, den Windows nicht speichern kann, sind nicht in der Sicherung: $2"],
    [/^Your data is (.+), more than a backup can hold \((.+)\)\. The biggest parts: (.+)\. Move or delete some of it, or copy it by hand\.$/,
      "Deine Daten sind $1 groß, mehr als eine Sicherung fassen kann ($2). Am größten: $3. Verschiebe oder lösche etwas davon, oder kopiere es von Hand."],
    [/^There are (.+) files, more than a backup can hold \((.+)\)\. The parts with most files: (.+)\. Move or delete some of them, or copy them by hand\.$/,
      "Es sind $1 Dateien, mehr als eine Sicherung fassen kann ($2). Die meisten Dateien: $3. Verschiebe oder lösche einige, oder kopiere sie von Hand."],
    [/^Some hidden files of your old notes are still in (.+)\.$/,
      "Einige versteckte Dateien deiner alten Notizen liegen noch in $1."],
  ];
  // Never translated: the conversation, code, and what you type.
  const KEEP = ".msg .body, pre, code, textarea, [translate=no], .notranslate";

  function swap(text) {
    const raw = text.trim();
    if (!raw) return null;
    // A sentence that wraps in the page's source has a line break and spaces inside it:
    // look it up with single spaces (the exact text first: a few keys keep two spaces).
    const has = (k) => Object.prototype.hasOwnProperty.call(DE, k);
    const key = has(raw) ? raw : raw.replace(/\s+/g, " ");
    let out = has(key) ? DE[key] : undefined;
    if (out === undefined) {
      for (const [pattern, replacement] of PATTERNS) {
        if (pattern.test(key)) { out = key.replace(pattern, replacement); break; }
      }
    }
    if (out === undefined) return null;
    return text.slice(0, text.length - text.trimStart().length) + out + text.slice(text.trimEnd().length);
  }
  window.starlightTranslate = (text) => swap(String(text)) ?? String(text);

  const ATTRIBUTES = ["placeholder", "title", "aria-label"];
  function attributes(element) {
    if (element.closest(".msg .body")) return;
    for (const name of ATTRIBUTES) {
      const value = element.getAttribute(name);
      if (!value) continue;
      const changed = swap(value);
      if (changed !== null && changed !== value) element.setAttribute(name, changed);
    }
  }
  function visit(node) {
    if (node.nodeType === Node.TEXT_NODE) {
      const parent = node.parentElement;
      if (!parent || parent.closest(KEEP) || parent.tagName === "SCRIPT" || parent.tagName === "STYLE") return;
      const changed = swap(node.nodeValue);
      if (changed !== null && changed !== node.nodeValue) node.nodeValue = changed;
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    attributes(node);
    if (node.closest(".msg .body")) return;
    for (const child of node.childNodes) visit(child);
  }
  const watcher = new MutationObserver((changes) => {
    for (const change of changes) {
      if (change.type === "childList") change.addedNodes.forEach(visit);
      else if (change.type === "characterData") visit(change.target);
      else if (change.type === "attributes") attributes(change.target);
    }
  });
  watcher.observe(document.documentElement, {
    childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRIBUTES,
  });
  document.addEventListener("DOMContentLoaded", () => visit(document.body));
})();
