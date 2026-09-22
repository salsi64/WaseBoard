namespace WaseBoard
{
    /// <summary>
    /// État de thème global et léger : évite de faire transiter le thème à travers tous les
    /// bindings XAML juste pour que le sélecteur de gabarit des boutons de son sache quel
    /// visuel choisir. Mis à jour par MainWindow à chaque changement dans les Paramètres.
    /// </summary>
    public static class ThemeState
    {
        public static bool IsModern { get; set; }

        /// <summary>"Grid" (défaut) ou "List" : bascule grille/liste, prioritaire sur IsModern dans SoundButtonTemplateSelector.</summary>
        public static string ViewMode { get; set; } = "Grid";
    }
}
