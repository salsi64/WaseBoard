using System.Collections.Generic;
using System.Linq;
using System.Windows;
using WaseBoard.Services;

namespace WaseBoard.Windows
{
    /// <summary>Choix du serveur Discord cible d'un upload, quand l'utilisateur appartient à
    /// plusieurs serveurs — inutile (et masqué par l'appelant) s'il n'en a qu'un.</summary>
    public partial class GuildPickerWindow : Window
    {
        /// <summary>Guilde choisie, ou null si annulé.</summary>
        public string? ResultGuildId { get; private set; }

        public GuildPickerWindow(IEnumerable<SoundLibraryService.SharedCategoryInfo> guilds, string? preselectGuildId)
        {
            InitializeComponent();
            GuildList.ItemsSource = guilds.ToList();

            var toSelect = GuildList.Items.Cast<SoundLibraryService.SharedCategoryInfo>()
                .FirstOrDefault(g => g.GuildId == preselectGuildId);
            if (toSelect is not null) GuildList.SelectedItem = toSelect;
        }

        private void GuildList_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e)
        {
            OkButton.IsEnabled = GuildList.SelectedItem is not null;
        }

        private void Ok_Click(object sender, RoutedEventArgs e)
        {
            if (GuildList.SelectedItem is SoundLibraryService.SharedCategoryInfo selected)
            {
                ResultGuildId = selected.GuildId;
                DialogResult = true;
                Close();
            }
        }

        private void Cancel_Click(object sender, RoutedEventArgs e)
        {
            ResultGuildId = null;
            DialogResult = false;
            Close();
        }
    }
}
