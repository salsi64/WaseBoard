using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Effects;
using System.Windows.Threading;
using Microsoft.Win32;
using WaseBoard.Models;
using WaseBoard.Services;
using WaseBoard.Services.MicFx;
using WaseBoard.Windows;
using WaseBoard.Services.Localization;

namespace WaseBoard
{
    // Barre latérale, sections de la liste, recherche et rafraîchissement des sondages.
    public partial class MainWindow
    {
        // ---------- Navigation (barre latérale, thème moderne) ----------

        private class NavItem
        {
            public string Label { get; set; } = "";
            public string? IconUrl { get; set; }
            public bool HasIcon => !string.IsNullOrEmpty(IconUrl);
            public int Index { get; set; }
            public string CategoryKey { get; set; } = "";
            public bool IsReorderable { get; set; }
        }

        private void NavHome_Click(object sender, RoutedEventArgs e) => SectionsScrollViewer.ScrollToTop();

        /// <summary>« ★ Favoris » : la section Favoris est toujours la première affichée.</summary>
        private void NavFavorites_Click(object sender, RoutedEventArgs e) => ScrollToSectionIndex(0);

        private void NavItem_Click(object sender, RoutedEventArgs e)
        {
            if (sender is Button { Tag: NavItem navItem }) ScrollToSectionIndex(navItem.Index);
        }

        private Point _navDragStart;
        private bool _isDraggingNav;
        private Point _sectionDragStart;
        private bool _isDraggingSection;

        private void NavItem_PreviewMouseLeftButtonDown(object sender, MouseButtonEventArgs e)
        {
            _navDragStart = e.GetPosition(null);
            _isDraggingNav = false;
        }

        private void NavItem_PreviewMouseMove(object sender, MouseEventArgs e)
        {
            if (e.LeftButton != MouseButtonState.Pressed || _isDraggingNav) return;
            if (sender is not Button { Tag: NavItem navItem } button) return;
            if (!navItem.IsReorderable) return; // seules les catégories personnelles se réordonnent ainsi

            var diff = _navDragStart - e.GetPosition(null);
            if (Math.Abs(diff.X) < SystemParameters.MinimumHorizontalDragDistance &&
                Math.Abs(diff.Y) < SystemParameters.MinimumVerticalDragDistance)
                return;

            _isDraggingNav = true;
            DragDrop.DoDragDrop(button, navItem, DragDropEffects.Move);
            _isDraggingNav = false;
        }

        private void NavItem_Drop(object sender, DragEventArgs e)
        {
            if (sender is not Button { Tag: NavItem targetNav }) return;
            if (e.Data.GetData(typeof(NavItem)) is not NavItem draggedNav) return;
            if (!draggedNav.IsReorderable || !targetNav.IsReorderable) return;
            if (ReferenceEquals(draggedNav, targetNav)) return;

            var order = _library.GetCategoriesInOrder(_sharedCategories.Select(s => s.GuildId));
            var oldIndex = order.IndexOf(draggedNav.CategoryKey);
            var newIndex = order.IndexOf(targetNav.CategoryKey);
            if (oldIndex < 0 || newIndex < 0) return;

            order.RemoveAt(oldIndex);
            order.Insert(newIndex, draggedNav.CategoryKey);
            _library.SaveCategoryOrder(order);
            RefreshSections();
            e.Handled = true;
        }

        private void ScrollToSectionIndex(int index)
        {
            if (index < 0 || index >= _currentSections.Count) return;
            SectionsItemsControl.UpdateLayout();
            if (SectionsItemsControl.ItemContainerGenerator.ContainerFromIndex(index) is FrameworkElement element)
                element.BringIntoView();
        }

        /// <summary>Réduit/agrandit la barre latérale pour gagner de la place, sans perdre l'accès à la navigation (le bouton de bascule reste visible).</summary>
        private void SidebarToggle_Click(object sender, RoutedEventArgs e)
        {
            _sidebarCollapsed = !_sidebarCollapsed;

            SidebarColumn.Width = new GridLength(_sidebarCollapsed ? 60 : 230);
            SidebarToggleButton.Content = _sidebarCollapsed ? "▶" : "◀";

            var visibility = _sidebarCollapsed ? Visibility.Collapsed : Visibility.Visible;
            SidebarHeaderText.Visibility = visibility;
            SidebarBody.Visibility = visibility;
            ApplyToolbarDensity();
        }

        private async Task RefreshCatalogAsync()
        {
            ServerStatusText.Text = "Connexion...";
            SetStatusDot(Color.FromRgb(0xF5, 0xA6, 0x23), pulsing: true);
            _hotkeys?.UnregisterAll();

            var items = await _library.FetchCatalogAsync();

            Sounds.Clear();
            foreach (var item in items) Sounds.Add(item);
            RegisterAllHotkeys();
            RefreshSections();

            // Ni le texte ni l'infobulle n'affichent l'adresse du serveur (confidentialité) : juste
            // l'état connecté/non, et le détail d'erreur en cas de souci.
            var connected = items.Count > 0 || string.IsNullOrEmpty(_library.LastErrorDetail);
            ServerStatusText.Text = connected ? Loc.T("main.server.connected", items.Count) : Loc.T("main.server.unreachable");
            ServerStatusText.ToolTip = connected ? null : _library.LastErrorDetail;
            SetStatusDot(connected ? Color.FromRgb(0x4C, 0xAF, 0x50) : Color.FromRgb(0xE8, 0x11, 0x23), pulsing: false);

            // PrecomputeWaveformsAsync télécharge déjà (en cache) chaque son non encore vu pour en
            // tirer la durée/waveform : un second passage de préchargement séparé téléchargerait les
            // mêmes fichiers une deuxième fois en parallèle (deux écritures concurrentes sur le même
            // chemin de cache) sans rien gagner, d'où sa suppression.
            _ = PrecomputeWaveformsAsync(items);
        }

        /// <summary>
        /// Calcule en arrière-plan (4 en parallèle max) les mini-waveforms manquantes, avec cache
        /// disque. Ne bloque jamais l'UI, chaque son se met à jour individuellement.
        /// </summary>
        private async Task PrecomputeWaveformsAsync(List<SoundItem> items)
        {
            using var throttle = new SemaphoreSlim(4);
            var tasks = items.Select(async item =>
            {
                if (item.WaveformPeaks is not null) return;
                await throttle.WaitAsync();
                try
                {
                    var localPath = await _library.GetOrDownloadCachedFileAsync(item);
                    if (localPath is null) return;
                    var mini = await Task.Run(() =>
                        AudioTrimService.GetOrComputeMiniWaveform(item.Id, localPath, _library.CacheFolder));
                    if (mini is not null)
                    {
                        // La durée du fichier COMPLET situe la portion gardée d'un son découpé sur sa waveform.
                        item.DurationMs = mini.DurationMs;
                        item.WaveformPeaks = mini.Peaks;
                    }
                }
                finally { throttle.Release(); }
            });
            await Task.WhenAll(tasks);
        }

        /// <summary>Pastille de statut serveur : pulse pendant la connexion, fixe une fois résolue.</summary>
        private void SetStatusDot(Color color, bool pulsing)
        {
            StatusDot.Fill = new SolidColorBrush(color);
            var pulse = (Storyboard)StatusDot.Resources["StatusDotPulse"];
            if (pulsing)
            {
                pulse.Begin(StatusDot, true);
            }
            else
            {
                pulse.Stop(StatusDot);
                StatusDot.Opacity = 1.0;
            }
        }

        /// <summary>Sondage régulier de l'activité partagée (qui joue quoi) et de la présence (qui a l'app ouverte), communs à tous les clients.</summary>
        /// <summary>Rythme du sondage d'activité : 300 ms tant que le serveur répond ; après 3 échecs de suite il passe à 2 s, 10 s puis 30 s
        /// (retour immédiat à 300 ms au premier succès). Un client dont la clé du serveur est refusée ne martèle plus le serveur, des heures durant.</summary>
        private readonly PollBackoff _activityBackoff = new(TimeSpan.FromMilliseconds(300),
            new[] { TimeSpan.FromSeconds(2), TimeSpan.FromSeconds(10), TimeSpan.FromSeconds(30) });
        private bool _keyRefusedNotified;

        /// <summary>Repart au rythme normal (réglages enregistrés, actualisation demandée) : pas la peine d'attendre le prochain palier.</summary>
        private void ResetActivityBackoff()
        {
            _keyRefusedNotified = false;
            if (_activityBackoff.Reset() && _activityTimer is not null) _activityTimer.Interval = _activityBackoff.Interval;
        }

        private async Task PollActivityAsync()
        {
            var (activity, online, status) = await _library.GetActivityAsync();

            if (status == ActivityPollStatus.Ok)
            {
                // Chemin normal : le compteur d'échecs est déjà à zéro, rien d'autre n'est touché (ni minuteur, ni interface).
                if (_activityBackoff.RecordSuccess() && _activityTimer is not null) _activityTimer.Interval = _activityBackoff.Interval;
                _keyRefusedNotified = false;
            }
            else
            {
                var firstFailure = _activityBackoff.ConsecutiveFailures == 0;
                if (_activityBackoff.RecordFailure() && _activityTimer is not null) _activityTimer.Interval = _activityBackoff.Interval;

                // Refus d'authentification de suite : l'appli ne peut rien jouer correctement, autant le dire (une fois par épisode).
                if (status == ActivityPollStatus.Unauthorized && _activityBackoff.IsSlowed && !_keyRefusedNotified)
                {
                    _keyRefusedNotified = true;
                    ToastService.Show(Loc.T("main.key_refused"), ToastKind.Warning);
                }

                // Premier échec : on applique « personne ne joue » comme avant. Les suivants ne changeraient plus rien : on s'épargne
                // le parcours de tout le catalogue à chaque tentative.
                if (!firstFailure) return;
            }

            // Construit la table de correspondance id->son PENDANT ce même parcours (plutôt qu'un
            // Sounds.FirstOrDefault(...) séparé par son actif plus bas) : sur un gros catalogue avec
            // plusieurs sons joués en même temps, ce sondage toutes les 300ms finissait par repasser
            // en boucle sur tout le catalogue pour chaque son actif (coût qui grossit avec les deux
            // à la fois), perceptible surtout en usage actif — exactement quand la réactivité compte.
            var soundsById = new Dictionary<string, SoundItem>(Sounds.Count);
            foreach (var item in Sounds)
            {
                soundsById[item.Id] = item;
                if (activity.TryGetValue(item.Id, out var users))
                {
                    item.IsPlaying = true;

                    var currentIds = item.ActiveUsers.Select(u => u.UserId).ToHashSet();
                    var newIds = users.Select(u => u.UserId).ToHashSet();

                    foreach (var stale in item.ActiveUsers.Where(u => !newIds.Contains(u.UserId)).ToList())
                        item.ActiveUsers.Remove(stale);
                    foreach (var fresh in users.Where(u => !currentIds.Contains(u.UserId)))
                        item.ActiveUsers.Add(fresh);
                }
                else
                {
                    item.IsPlaying = _micPlayingIds.Contains(item.Id);
                    if (item.ActiveUsers.Count > 0) item.ActiveUsers.Clear();
                }
            }

            // Barre "now playing" : liste plate (son, utilisateur), dérivée de la même activité et
            // réconciliée par clé (même technique anti-flicker qu'au-dessus) plutôt que reconstruite.
            var freshEntries = activity
                .SelectMany(kv => kv.Value.Select(u => (SoundId: kv.Key, User: u)))
                .Select(t =>
                {
                    soundsById.TryGetValue(t.SoundId, out var sound);
                    return new NowPlayingEntry
                    {
                        SoundId = t.SoundId,
                        UserId = t.User.UserId,
                        SoundName = sound?.Name ?? "?",
                        Emoji = sound?.Emoji,
                        Username = t.User.Username,
                        AvatarUrl = t.User.AvatarUrl
                    };
                })
                .ToList();
            var freshEntryKeys = freshEntries.Select(x => x.Key).ToHashSet();
            var currentEntryKeys = NowPlayingEntries.Select(x => x.Key).ToHashSet();
            foreach (var stale in NowPlayingEntries.Where(x => !freshEntryKeys.Contains(x.Key)).ToList())
                NowPlayingEntries.Remove(stale);
            foreach (var fresh in freshEntries.Where(x => !currentEntryKeys.Contains(x.Key)))
                NowPlayingEntries.Add(fresh);

            // Mise à jour de la barre "en ligne" des catégories partagées, sans reconstruire toute
            // l'interface (juste les collections des sections déjà affichées) pour rester fluide.
            var onlineIds = online.Select(u => u.UserId).ToHashSet();
            foreach (var section in _currentSections.Where(s => s.IsShared))
            {
                var currentIds = section.OnlineUsers.Select(u => u.UserId).ToHashSet();
                foreach (var stale in section.OnlineUsers.Where(u => !onlineIds.Contains(u.UserId)).ToList())
                    section.OnlineUsers.Remove(stale);
                foreach (var fresh in online.Where(u => !currentIds.Contains(u.UserId)))
                    section.OnlineUsers.Add(fresh);
            }

            // Met en valeur, dans la barre latérale, l'avatar de qui que ce soit du salon vocal
            // actuellement en train de jouer un son (même logique d'activité que les boutons).
            var activeUserIds = activity.Values.SelectMany(list => list.Select(u => u.UserId)).ToHashSet();
            foreach (var member in VoiceChannelMembers)
                member.IsActive = activeUserIds.Contains(member.UserId);
        }

        /// <summary>Récupère les catégories partagées par votre serveur Discord, et rafraîchit l'affichage.</summary>
        private async Task RefreshSharedCategoriesAsync()
        {
            _sharedCategories = await _library.FetchSharedCategoriesAsync();
            RefreshSections();
        }

        /// <summary>Reconstruit les sections affichées (★ Favoris, catégories personnelles, une par guilde Discord), en appliquant la recherche en cours.</summary>
        private void RefreshSections()
        {
            IEnumerable<SoundItem> Filtered(IEnumerable<SoundItem> src) =>
                string.IsNullOrWhiteSpace(_searchQuery)
                    ? src
                    : src.Where(s => s.Name.Contains(_searchQuery, StringComparison.OrdinalIgnoreCase));

            var sections = new List<SectionViewModel>
            {
                new SectionViewModel
                {
                    Name = Loc.T("main.nav.favorites"),
                    CategoryKey = FavoritesKey,
                    Sounds = new ObservableCollection<SoundItem>(Filtered(Sounds.Where(s => s.IsFavorite))),
                    IsManageable = false,
                    IsExpanded = !_library.Settings.CollapsedSections.Contains(FavoritesKey)
                }
            };

            // Ordre unifié (personnelles + partagées) : permet de réordonner les catégories
            // partagées au même titre que les personnelles, avec les mêmes flèches ↑/↓.
            foreach (var key in _library.GetCategoriesInOrder(_sharedCategories.Select(s => s.GuildId)))
            {
                if (_library.Settings.Categories.ContainsKey(key))
                {
                    sections.Add(new SectionViewModel
                    {
                        Name = key,
                        CategoryKey = key,
                        Sounds = new ObservableCollection<SoundItem>(Filtered(_library.GetSoundsInCategory(key, Sounds))),
                        IsManageable = true,
                        IsReorderable = true,
                        IsExpanded = !_library.Settings.CollapsedSections.Contains(key)
                    });
                    continue;
                }

                var shared = _sharedCategories.FirstOrDefault(s => s.GuildId == key);
                if (shared is null) continue;

                // Section native de la guilde : union des sons dont c'est la guilde d'origine
                // ET de ceux partagés manuellement dans sa catégorie (shared_categories.json) —
                // remplace l'ancienne vue "partagés uniquement", cohérent avec le filtrage serveur.
                var idSet = new HashSet<string>(shared.SoundIds);
                var guildSounds = Sounds.Where(s => s.GuildId == shared.GuildId || idSet.Contains(s.Id));
                sections.Add(new SectionViewModel
                {
                    Name = shared.GuildName,
                    CategoryKey = shared.GuildId,
                    Sounds = new ObservableCollection<SoundItem>(SortAllSounds(Filtered(guildSounds))),
                    IsManageable = false, // catégorie automatique : pas de renommage/suppression manuel
                    IsShared = true,
                    IsReorderable = true,
                    IsAllSounds = true, // affiche le sélecteur de tri dans l'en-tête
                    IconUrl = shared.IconUrl,
                    IsExpanded = !_library.Settings.CollapsedSections.Contains(shared.GuildId)
                });
            }

            _currentSections = sections;
            SectionsItemsControl.ItemsSource = sections;

            // Navigation de la barre latérale : une entrée par section réellement affichée. Pour
            // les catégories partagées, l'icône du serveur Discord est affichée à côté du nom (pas d'emoji générique).
            // Favoris a son propre bouton (à côté d'Accueil) : seules les catégories et les serveurs ont des groupes.
            var navItems = sections
                .Select((s, i) =>
                {
                    var iconUrl = s.IsShared ? _sharedCategories.FirstOrDefault(sc => sc.GuildId == s.CategoryKey)?.IconUrl : null;
                    return (Section: s, Item: new NavItem
                    {
                        Label = iconUrl is not null ? StripSharedPrefix(s.Name) : s.Name,
                        IconUrl = iconUrl,
                        Index = i,
                        CategoryKey = s.CategoryKey,
                        IsReorderable = s.IsReorderable
                    });
                })
                .Where(t => t.Section.CategoryKey != FavoritesKey)
                .ToList();

            var categories = navItems.Where(t => !t.Section.IsShared).Select(t => t.Item).ToList();
            var servers = navItems.Where(t => t.Section.IsShared).Select(t => t.Item).ToList();
            SidebarCategoriesList.ItemsSource = categories;
            SidebarServersList.ItemsSource = servers;

            // Un groupe vide n'apparaît pas ; l'état replié/déplié est mémorisé (CollapsedSections, comme les sections).
            _updatingNavGroups = true;
            NavCategoriesCount.Text = categories.Count.ToString();
            NavServersCount.Text = servers.Count.ToString();
            NavCategoriesGroup.Visibility = categories.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
            NavServersGroup.Visibility = servers.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
            NavCategoriesToggle.IsChecked = !_library.Settings.CollapsedSections.Contains(NavCategoriesGroupKey);
            NavServersToggle.IsChecked = !_library.Settings.CollapsedSections.Contains(NavServersGroupKey);
            _updatingNavGroups = false;
        }

        private const string NavCategoriesGroupKey = "__nav_categories__";
        private const string NavServersGroupKey = "__nav_servers__";
        private bool _updatingNavGroups;

        private void NavGroupToggle_Changed(object sender, RoutedEventArgs e)
        {
            // _settingsReady : ce gestionnaire se déclenche aussi pendant InitializeComponent(), avant le chargement des réglages.
            if (!_settingsReady || _updatingNavGroups || sender is not ToggleButton toggle) return;

            var key = ReferenceEquals(toggle, NavCategoriesToggle) ? NavCategoriesGroupKey : NavServersGroupKey;
            var collapsed = _library.Settings.CollapsedSections;
            if (toggle.IsChecked == true) collapsed.Remove(key);
            else if (!collapsed.Contains(key)) collapsed.Add(key);
            _library.SaveSettings();
        }

        /// <summary>Tri appliqué à chaque section de guilde (les seules vouées à devenir vraiment
        /// longues) : "Custom" garde l'ordre d'affichage actuel (glisser-déposer manuel, celui de
        /// Sounds), les autres trient par nom. Choix mémorisé (Settings.AllSoundsSortMode).</summary>
        private static IEnumerable<SoundItem> SortAllSounds(IEnumerable<SoundItem> sounds, string sortMode) => sortMode switch
        {
            "NameAsc" => sounds.OrderBy(s => s.Name, StringComparer.OrdinalIgnoreCase),
            "NameDesc" => sounds.OrderByDescending(s => s.Name, StringComparer.OrdinalIgnoreCase),
            _ => sounds
        };

        private IEnumerable<SoundItem> SortAllSounds(IEnumerable<SoundItem> sounds) =>
            SortAllSounds(sounds, _library.Settings.AllSoundsSortMode);

        /// <summary>Présélectionne le tri courant à chaque recréation du ComboBox (pas de binding
        /// réactif, les sections étant reconstruites à chaque RefreshSections()).</summary>
        private void AllSoundsSortCombo_Loaded(object sender, RoutedEventArgs e)
        {
            if (sender is not ComboBox { DataContext: SectionViewModel { IsAllSounds: true } } combo) return;

            var mode = _library.Settings.AllSoundsSortMode;
            foreach (ComboBoxItem item in combo.Items)
            {
                if ((string)item.Tag == mode) { combo.SelectedItem = item; return; }
            }
            combo.SelectedIndex = 0;
        }

        private void AllSoundsSort_SelectionChanged(object sender, SelectionChangedEventArgs e)
        {
            if (sender is not ComboBox { SelectedItem: ComboBoxItem { Tag: string mode } } || !_settingsReady) return;
            if (mode == _library.Settings.AllSoundsSortMode) return;

            _library.Settings.AllSoundsSortMode = mode;
            _library.SaveSettings();
            RefreshSections();
        }

        private void SearchBox_TextChanged(object sender, TextChangedEventArgs e)
        {
            _searchQuery = SearchBox.Text;
            RefreshSections();
        }

        private async void RefreshButton_Click(object sender, RoutedEventArgs e)
        {
            ResetActivityBackoff();
            await RefreshCatalogAsync();
        }

        private void Window_Closing(object sender, CancelEventArgs e)
        {
            _activityTimer?.Stop();
            _voiceStatusTimer?.Stop();
            _themeTimer?.Stop();
            _micDiagTimer?.Stop();
            _hotkeys?.Dispose();
            _clip?.Dispose();
            _audio.Dispose();
            _micFeed.Dispose();
            ToastService.Requested -= OnToastRequested;
        }
    }
}
