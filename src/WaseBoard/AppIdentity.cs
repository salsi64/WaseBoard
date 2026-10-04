namespace WaseBoard
{
    /// <summary>Identité de l'application (nom, dossier AppData, mutex mono-instance), différente
    /// en configuration Dev (voir WaseBoard.csproj) pour pouvoir tester contre le serveur/bot
    /// Discord isolés sans jamais toucher aux réglages ni entrer en conflit avec l'installation
    /// WaseBoard normale.</summary>
    internal static class AppIdentity
    {
#if WASEBOARD_DEV
        public const string Name = "WaseBoard-Dev";
        public const string DataFolderName = "WaseBoard-Dev";
        public const string MutexName = "WaseBoardDev_SingleInstance_Mutex";

        // Pas de serveur par défaut en Dev : le build Dev pointe vers le serveur/bot de test,
        // configuré manuellement une fois (voir settings.json du dossier WaseBoard-Dev), jamais
        // écrasé automatiquement.
        public const string? DefaultServerUrl = null;
        public const string? DefaultServerToken = null;
#else
        public const string Name = "WaseBoard";
        public const string DataFolderName = "WaseBoard";
        public const string MutexName = "WaseBoard_SingleInstance_Mutex";

        // Serveur public par défaut (waseboard.salsi.bid) : intégré au client pour qu'un premier
        // lancement se connecte sans lien Discord ni saisie manuelle (voir SoundLibraryService.
        // LoadFromDisk). Le bot étant public, ce secret est déjà obtenable légitimement par
        // quiconque ajoute le bot à un serveur puis tape /configurer-invitation -- l'intégrer ici
        // ne change rien en pratique, décision prise avec l'hébergeur. Si ce secret est tourné un
        // jour, TOUS les clients avec ce défaut cessent de fonctionner tant qu'ils n'ont pas migré
        // vers une nouvelle version (voir OnboardingReason.ServerReconfigNeeded, qui oriente alors
        // vers une mise à jour plutôt qu'une ressaisie manuelle).
        public const string DefaultServerUrl = "https://waseboard.salsi.bid";
        public const string DefaultServerToken = "b0c81e81b66f1dacdd4bcf1643e78d37321f5c3fd31383ce29259167e6975c5e";
#endif
    }
}
