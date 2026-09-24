# wissen.py

WISSENSDATENBANK = {
    "tickets": {
        "wie erstelle ich ein ticket":
            "Du kannst über das Ticketsystem auf dem Dresden-RP Discord-Server ein Ticket erstellen.",

        "wofür ist ein ticket":
            "Ein Ticket ist für Fragen, Probleme oder persönliche Anliegen an das Support-Team gedacht.",

        "wann wird mein ticket beantwortet":
            "Die Bearbeitungszeit kann unterschiedlich sein. Bitte habe etwas Geduld, bis sich ein Support-Mitglied um dein Anliegen kümmert.",

        "ticket schließen":
            "Wenn dein Anliegen geklärt wurde, kann das Ticket geschlossen werden.",
    },

    "bewerbungen": {
        "wie werde ich supporter":
            "Wenn Bewerbungen geöffnet sind, kannst du dich über das offizielle Bewerbungsverfahren von Dresden RP bewerben.",

        "wie kann ich mich bewerben":
            "Die Bewerbung erfolgt über das offizielle Bewerbungsverfahren von Dresden RP.",

        "kann ich mich als moderator bewerben":
            "Wenn Bewerbungen für Moderatoren geöffnet sind, kannst du dich über das offizielle Bewerbungsverfahren bewerben.",
    },

    "bot": {
        "was kann der bot":
            "Der Dresden-RP-Bot bietet unter anderem Tickets, Guthaben, Shop, Dienste, Giveaways und weitere Serverfunktionen.",

        "was ist das guthaben":
            "Das Guthaben ist das interne Währungssystem des Dresden-RP-Bots.",

        "was ist der shop":
            "Über den Shop können verfügbare Artikel und Belohnungen des Servers erworben werden.",
    },

    "server": {
        "was ist dresden rp":
            "Dresden RP ist ein Roblox-Roleplay-Projekt mit einem zugehörigen Discord-Server und verschiedenen RP- und Bot-Systemen.",
    }
}


def suche_antwort(frage: str):
    """
    Sucht nach einer passenden Antwort in der Wissensdatenbank.
    """

    frage = frage.lower().strip()

    for kategorie, fragen in WISSENSDATENBANK.items():

        for suchbegriff, antwort in fragen.items():

            if suchbegriff in frage:
                return {
                    "gefunden": True,
                    "kategorie": kategorie,
                    "antwort": antwort
                }

    return {
        "gefunden": False,
        "kategorie": None,
        "antwort": None
    }
