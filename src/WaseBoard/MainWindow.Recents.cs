using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Windows.Threading;
using WaseBoard.Models;
using WaseBoard.Services;
using WaseBoard.Services.Localization;

namespace WaseBoard
{
    // Section « Récents » : vos derniers sons joués, en tête de liste après les favoris.
    public partial class MainWindow
    {
        private DispatcherTimer? _recentsSaveTimer;

        /// <summary>Note un son joué dans l'historique (réglages) et met la section à jour. <paramref name="keepDisplayedOrder"/> :
        /// le clic vient de la section « Récents » elle-même — le bouton ne doit pas sauter sous la souris, l'historique est
        /// mis à jour mais l'ordre affiché ne change qu'au prochain rafraîchissement.</summary>
        private void RememberRecent(SoundItem item, bool keepDisplayedOrder)
        {
            if (!_library.Settings.ShowRecents) return;
            if (!RecentSounds.Push(_library.Settings.RecentSoundIds, item.Id)) return;

            ScheduleRecentsSave();
            if (!keepDisplayedOrder) UpdateRecentsSectionInPlace(item);
        }

        /// <summary>Met à jour la section sans reconstruire la page (une reconstruction relancerait les animations et ferait
        /// clignoter les boutons à chaque clic) ; seule la toute première lecture, qui crée la section, la reconstruit.</summary>
        private void UpdateRecentsSectionInPlace(SoundItem item)
        {
            var section = _currentSections.FirstOrDefault(s => s.CategoryKey == RecentsKey);
            if (section is null)
            {
                RefreshSections();
                return;
            }
            if (!string.IsNullOrWhiteSpace(_searchQuery)) return; // recherche en cours : la section reflète le filtre, pas l'historique

            var index = section.Sounds.IndexOf(item);
            if (index > 0)
            {
                section.Sounds.Move(index, 0);
            }
            else if (index < 0)
            {
                section.Sounds.Insert(0, item);
                while (section.Sounds.Count > RecentSounds.MaxShown) section.Sounds.RemoveAt(section.Sounds.Count - 1);
            }
        }

        /// <summary>Les réglages sont écrits une seule fois, un instant après le dernier clic (pas à chaque son joué).</summary>
        private void ScheduleRecentsSave()
        {
            _recentsSaveTimer ??= new DispatcherTimer { Interval = TimeSpan.FromSeconds(2) };
            _recentsSaveTimer.Tick -= RecentsSaveTimer_Tick;
            _recentsSaveTimer.Tick += RecentsSaveTimer_Tick;
            _recentsSaveTimer.Stop();
            _recentsSaveTimer.Start();
        }

        private void RecentsSaveTimer_Tick(object? sender, EventArgs e)
        {
            _recentsSaveTimer?.Stop();
            _library.SaveSettings();
        }

        private void ClearRecents()
        {
            _library.Settings.RecentSoundIds.Clear();
            _library.SaveSettings();
            RefreshSections();
        }

        /// <summary>La section « Récents » à afficher (null s'il n'y a rien à montrer ou si elle est masquée). Les sons qui
        /// n'existent plus au catalogue sont ignorés ; la recherche en cours s'applique comme aux autres sections.</summary>
        private SectionViewModel? BuildRecentsSection(Func<IEnumerable<SoundItem>, IEnumerable<SoundItem>> filter)
        {
            if (!_library.Settings.ShowRecents) return null;

            var byId = Sounds.GroupBy(s => s.Id).ToDictionary(g => g.Key, g => g.First());
            var recents = RecentSounds.Resolve(_library.Settings.RecentSoundIds, id => byId.GetValueOrDefault(id), RecentSounds.MaxShown);
            var shown = filter(recents).ToList();
            if (shown.Count == 0) return null;

            return new SectionViewModel
            {
                Name = Loc.T("section.recents"),
                CategoryKey = RecentsKey,
                Sounds = new ObservableCollection<SoundItem>(shown),
                IsManageable = false,
                IsExpanded = !_library.Settings.CollapsedSections.Contains(RecentsKey)
            };
        }
    }
}
