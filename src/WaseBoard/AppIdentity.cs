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
#else
        public const string Name = "WaseBoard";
        public const string DataFolderName = "WaseBoard";
        public const string MutexName = "WaseBoard_SingleInstance_Mutex";
#endif
    }
}
