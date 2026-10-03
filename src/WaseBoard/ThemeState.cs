using System.ComponentModel;

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

        /// <summary>Partie observable de l'état (liée depuis les gabarits des boutons de son).</summary>
        public static ThemeOptions Options { get; } = new();
    }

    /// <summary>Options d'affichage modifiables à chaud : les boutons de son se mettent à jour sans être reconstruits.</summary>
    public sealed class ThemeOptions : INotifyPropertyChanged
    {
        private bool _showWaveforms = true;

        /// <summary>Mini-waveform visible sur les boutons de son (faux = boutons compacts).</summary>
        public bool ShowWaveforms
        {
            get => _showWaveforms;
            set
            {
                if (_showWaveforms == value) return;
                _showWaveforms = value;
                PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(ShowWaveforms)));
            }
        }

        public event PropertyChangedEventHandler? PropertyChanged;
    }
}
