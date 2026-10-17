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
    // Ajout, édition, suppression et gestion des sons.
    public partial class MainWindow
    {
        // ---------- Ajout / gestion des sons ----------

        private static readonly string[] SupportedExtensions = { ".mp3", ".wav", ".ogg", ".flac", ".m4a", ".wma" };

        private async void AddSoundButton_Click(object sender, RoutedEventArgs e)
        {
            var dialog = new OpenFileDialog
            {
                Title = Loc.T("sounds.pick.title"),
                Filter = Loc.T("sounds.pick.filter"),
                Multiselect = true
            };

            if (dialog.ShowDialog(this) != true) return;

            await AddSoundFiles(dialog.FileNames);
        }

        /// <summary>Ajoute des fichiers audio au catalogue (fenêtre de découpe, doublons, envoi). <paramref name="suggestedName"/> :
        /// nom proposé à la place de celui du fichier (clip des dernières secondes) ; <paramref name="deleteFilesAfter"/> :
        /// fichiers temporaires à supprimer ensuite, que l'ajout ait abouti ou non.</summary>
        private async Task AddSoundFiles(IEnumerable<string> filePaths, string? suggestedName = null, bool deleteFilesAfter = false)
        {
            var files = filePaths.ToList();
            try
            {
                await AddSoundFilesCore(files, suggestedName);
            }
            finally
            {
                if (deleteFilesAfter)
                {
                    foreach (var file in files) TryDeleteFile(file);
                }
            }
        }

        private async Task AddSoundFilesCore(List<string> filePaths, string? suggestedNameOverride)
        {
            string? guildId;
            if (_sharedCategories.Count == 0)
            {
                ToastService.Show(
                    Loc.T("sounds.add.no_server"),
                    ToastKind.Error);
                return;
            }

            // Seuls les serveurs où l'upload est permis (pas réservé aux admins, pas bloqué) sont proposés.
            var uploadableGuilds = _sharedCategories.Where(s => s.CanUpload).ToList();
            if (uploadableGuilds.Count == 0)
            {
                ToastService.Show(
                    Loc.T("sounds.add.not_allowed"),
                    ToastKind.Warning);
                return;
            }
            else if (uploadableGuilds.Count == 1)
            {
                guildId = uploadableGuilds[0].GuildId;
            }
            else
            {
                // Présélectionne le serveur où l'utilisateur est actuellement connecté en vocal
                // (le cas le plus probable), les autres restant choisissables dans la liste.
                var (_, _, currentGuildId, _, _, _) = await _library.GetLiveVoiceStatusAsync();
                var guildPicker = new GuildPickerWindow(uploadableGuilds, currentGuildId) { Owner = this };
                if (guildPicker.ShowDialog() != true || guildPicker.ResultGuildId is null) return;
                guildId = guildPicker.ResultGuildId;
            }

            // Détection de doublons scopée à la guilde ciblée : un son identique existant dans une
            // autre guilde (que l'utilisateur ne partage pas forcément avec tout le monde) ne doit
            // pas être signalé comme doublon ici.
            var targetGuildSharedIds = new HashSet<string>(
                _sharedCategories.FirstOrDefault(s => s.GuildId == guildId)?.SoundIds ?? new List<string>());
            var soundsInTargetGuild = Sounds.Where(s => s.GuildId == guildId || targetGuildSharedIds.Contains(s.Id)).ToList();

            var rejected = new List<string>();

            foreach (var file in filePaths)
            {
                var extension = Path.GetExtension(file);
                if (!SupportedExtensions.Contains(extension, StringComparer.OrdinalIgnoreCase))
                {
                    rejected.Add(Path.GetFileName(file));
                    continue;
                }

                var suggestedName = suggestedNameOverride ?? Path.GetFileNameWithoutExtension(file);
                var trimWindow = new TrimWindow(file, suggestedName) { Owner = this };

                if (trimWindow.ShowDialog() != true)
                    continue;

                // Détection de doublons : hash du fichier SOURCE (le serveur garde le son complet, la découpe n'est
                // qu'un repère) contre le catalogue existant ; à défaut, un même nom reste un signal plus faible.
                var contentHash = SoundLibraryService.ComputeFileHash(file);
                var hashMatch = soundsInTargetGuild.FirstOrDefault(s => s.ContentHash is not null && s.ContentHash == contentHash);
                var nameMatch = hashMatch is null
                    ? soundsInTargetGuild.FirstOrDefault(s => string.Equals(s.Name, trimWindow.ResultName, StringComparison.OrdinalIgnoreCase))
                    : null;

                if (hashMatch is not null)
                {
                    var proceed = ConfirmDialog.Show(this,
                        Loc.T("sounds.add.duplicate_hash", hashMatch.Name) + "\n" +
                        Loc.T("sounds.add.duplicate_hash_hint"));
                    if (!proceed) continue;
                }
                else if (nameMatch is not null)
                {
                    var proceed = ConfirmDialog.Show(this,
                        Loc.T("sounds.add.duplicate_name", nameMatch.Name));
                    if (!proceed) continue;
                }

                // Fichier original + portion gardée + emoji, en un seul appel (plus de 2ᵉ fenêtre d'emoji après l'envoi).
                var uploaded = await _library.UploadSoundAsync(file, trimWindow.ResultName, guildId,
                    trimWindow.ResultTrimStartMs, trimWindow.ResultTrimEndMs, trimWindow.ResultEmoji);

                if (uploaded is not null)
                {
                    Sounds.Add(uploaded);
                    RegisterHotkeyFor(uploaded);
                }
                else
                {
                    ToastService.Show(
                        Loc.T("sounds.add.upload_failed", trimWindow.ResultName) +
                        (string.IsNullOrEmpty(_library.LastErrorDetail) ? "" : " " + _library.LastErrorDetail),
                        ToastKind.Error);
                }
            }

            RefreshSections();

            if (rejected.Count > 0)
            {
                ToastService.Show(
                    Loc.T("sounds.add.rejected", string.Join(", ", rejected)),
                    ToastKind.Warning);
            }
        }

        private void SoundButton_Click(object sender, RoutedEventArgs e)
        {
            if (sender is Button { Tag: SoundItem item } button)
            {
                AnimationHelpers.PlayBounce(button);
                Play(item);
            }
        }

        private void PreviewIcon_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
        {
            e.Handled = true; // empêche le clic principal du bouton parent de se déclencher aussi
            if (sender is FrameworkElement { DataContext: SoundItem item })
                PreviewLocal(item);
        }

        /// <summary>Clic droit sur un son : « Éditer… » regroupe désormais tout ce qui se règle sur le son (nom, emoji,
        /// favori, catégories, volume, couleur, découpe, fichier, suppression) ; le menu ne garde que ce qui dépend de
        /// l'endroit où l'on a cliqué (retirer de cette section) et le raccourci clavier.</summary>
        private void SoundButton_RightClick(object sender, MouseButtonEventArgs e)
        {
            if (sender is not Button { Tag: SoundItem item } button) return;

            var menu = new ContextMenu();

            var editItem = new MenuItem { Header = Loc.T("sounds.menu.edit") };
            editItem.Click += async (_, _) => await EditSoundAsync(item);
            menu.Items.Add(editItem);

            // Retirer de la catégorie EXACTE d'où l'on a cliqué (pas "toutes les catégories"),
            // retrouvée en remontant l'arbre visuel jusqu'au conteneur de la section — un même
            // son étant potentiellement affiché dans plusieurs sections à la fois.
            var section = FindEnclosingSection(button);
            // Pas de "Retirer" pour un son dans sa guilde d'origine : ce n'est pas un ajout
            // manuel à retirer (voir la suppression, dans la fenêtre « Éditer », pour l'effacer réellement).
            // Dans une catégorie partagée, seuls l'auteur du son ou un admin du serveur peuvent le retirer.
            var isNativeToGuildSection = section is not null && section.IsShared && item.GuildId == section.CategoryKey;
            var canRemoveFromSection = section is not null
                && (!section.IsShared || CanShareTo(_sharedCategories.FirstOrDefault(s => s.GuildId == section.CategoryKey), item));
            if (section is not null && !isNativeToGuildSection && canRemoveFromSection && section.CategoryKey != FavoritesKey)
            {
                var label = section.IsShared
                    ? Loc.T("sounds.menu.remove_from_shared", StripSharedPrefix(section.Name))
                    : Loc.T("sounds.menu.remove_from", section.Name);

                var removeFromSectionItem = new MenuItem { Header = label };
                removeFromSectionItem.Click += async (_, _) =>
                {
                    if (section.IsShared)
                    {
                        if (!await _library.SetSharedCategorySoundAsync(section.CategoryKey, item, false))
                            ToastService.Show(Loc.T("sounds.remove_from_category_failed") + " " + _library.LastErrorDetail, ToastKind.Warning);
                        await RefreshSharedCategoriesAsync();
                    }
                    else
                    {
                        _library.RemoveSoundFromCategory(section.CategoryKey, item);
                        RefreshSections();
                    }
                };
                menu.Items.Add(removeFromSectionItem);
            }

            var hotkeyItem = new MenuItem { Header = string.IsNullOrEmpty(item.Hotkey) ? Loc.T("sounds.menu.set_hotkey") : Loc.T("sounds.menu.change_hotkey", item.Hotkey) };
            hotkeyItem.Click += (_, _) => AssignHotkey(item);

            var removeHotkeyItem = new MenuItem { Header = Loc.T("sounds.menu.remove_hotkey"), IsEnabled = !string.IsNullOrEmpty(item.Hotkey) };
            removeHotkeyItem.Click += (_, _) =>
            {
                _library.SetHotkey(item, null);
                ResyncHotkeys();
            };

            menu.Items.Add(new Separator());
            menu.Items.Add(hotkeyItem);
            menu.Items.Add(removeHotkeyItem);
            menu.IsOpen = true;
        }

        /// <summary>Supprime un son du catalogue partagé (la confirmation est demandée par l'appelant).</summary>
        private async Task DeleteSoundAsync(SoundItem item)
        {
            var ok = await _library.DeleteSoundAsync(item);
            if (ok)
            {
                // Après le retrait (pas avant) : ResyncHotkeys() reconstruit depuis Sounds, donc
                // le son doit déjà être absent pour que son raccourci disparaisse vraiment.
                Sounds.Remove(item);
                ResyncHotkeys();
                RefreshSections();
            }
            else
            {
                ToastService.Show(
                    Loc.T("sounds.delete_failed") + (string.IsNullOrEmpty(_library.LastErrorDetail) ? "" : " " + _library.LastErrorDetail),
                    ToastKind.Warning);
            }
        }

        /// <summary>« Éditer… » : rouvre la fenêtre d'édition sur le son COMPLET (gardé par le serveur) avec la portion
        /// actuelle déjà placée ; nom et emoji sont modifiables au passage. Un seul appel au serveur pour tout enregistrer.</summary>
        private async Task EditSoundAsync(SoundItem item)
        {
            // Son d'un autre (ni auteur ni admin) : la fenêtre ne propose que les réglages locaux, sans besoin du fichier.
            var canEdit = item.CanEdit;
            var localPath = "";
            if (canEdit)
            {
                var downloaded = await _library.GetOrDownloadCachedFileAsync(item);
                if (downloaded is null)
                {
                    ToastService.Show(
                        Loc.T("sounds.load_full_failed") + (string.IsNullOrEmpty(_library.LastErrorDetail) ? "" : " " + _library.LastErrorDetail),
                        ToastKind.Warning);
                    return;
                }
                localPath = downloaded;
            }

            TimeSpan? start = item.TrimStartMs is { } s ? TimeSpan.FromMilliseconds(s) : null;
            TimeSpan? end = item.TrimEndMs is { } e ? TimeSpan.FromMilliseconds(e) : null;
            var localCategories = _library.GetCategoriesInOrder();
            var extras = new EditExtras
            {
                CanEditShared = canEdit,
                CanReplaceFile = _library.ServerSupports("replace_file"),
                Volume = item.Volume,
                IsFavorite = item.IsFavorite,
                ColorHex = item.ColorHex,
                FileName = item.Name + item.Extension,
                LocalCategories = localCategories,
                SelectedLocalCategories = localCategories
                    .Where(c => _library.Settings.Categories.TryGetValue(c, out var ids) && ids.Contains(item.Id)).ToList(),
                SharedCategories = _sharedCategories.Where(s => CanShareTo(s, item))
                    .Select(s => new SharedCategoryChoice
                    {
                        GuildId = s.GuildId,
                        GuildName = StripSharedPrefix(s.GuildName),
                        IsMember = s.SoundIds.Contains(item.Id) || item.GuildId == s.GuildId,
                        Locked = item.GuildId == s.GuildId
                    }).ToList()
            };
            var window = new TrimWindow(localPath, item.Name, TrimWindowMode.Edit, start, end, item.Emoji, extras) { Owner = this };
            if (window.ShowDialog() != true) return;

            // Suppression demandée (et déjà confirmée) depuis la ligne « Son » : plus rien d'autre à appliquer.
            if (window.ResultDeleteRequested)
            {
                await DeleteSoundAsync(item);
                return;
            }

            // Nouveau fichier audio : en premier, car le serveur retire alors la découpe (elle visait l'ancien fichier) et la
            // découpe choisie dans la fenêtre vise déjà le nouveau ; la confirmation a eu lieu dans la fenêtre.
            var fileReplaced = false;
            if (window.ResultReplacementFile is { } replacementFile)
            {
                if (!await _library.ReplaceSoundFileAsync(item, replacementFile))
                {
                    ToastService.Show(
                        Loc.T("sounds.replace_failed") + (string.IsNullOrEmpty(_library.LastErrorDetail) ? "" : " " + _library.LastErrorDetail),
                        ToastKind.Warning);
                    return;
                }
                fileReplaced = true;
                await PrecomputeWaveformsAsync(new List<SoundItem> { item });
            }

            // Réglages locaux (volume, couleur, favori, catégories personnelles) : appliqués tout de suite, sans serveur.
            var localChanged = fileReplaced;
            if (Math.Abs(window.ResultVolume - item.Volume) > 0.001f)
            {
                _library.SetVolume(item, window.ResultVolume);
                localChanged = true;
            }
            if (!string.Equals(window.ResultColorHex, item.ColorHex, StringComparison.OrdinalIgnoreCase))
            {
                _library.SetColor(item, window.ResultColorHex);
                localChanged = true;
            }
            if (window.ResultFavorite != item.IsFavorite)
            {
                _library.ToggleFavorite(item);
                localChanged = true;
            }
            var wantedLocal = new HashSet<string>(window.ResultLocalCategories);
            foreach (var category in _library.GetCategoriesInOrder().Concat(wantedLocal).Distinct())
            {
                var isMember = _library.Settings.Categories.TryGetValue(category, out var ids) && ids.Contains(item.Id);
                if (wantedLocal.Contains(category) && !isMember) { _library.AddSoundToCategory(category, item); localChanged = true; }
                else if (!wantedLocal.Contains(category) && isMember) { _library.RemoveSoundFromCategory(category, item); localChanged = true; }
            }

            // Catégories partagées des serveurs : une requête par changement (le serveur re-vérifie les droits).
            var sharedFailed = false;
            var sharedChanged = false;
            foreach (var choice in window.ResultSharedCategories.Where(c => !c.Locked))
            {
                var wasMember = _sharedCategories.FirstOrDefault(s => s.GuildId == choice.GuildId)?.SoundIds.Contains(item.Id) ?? false;
                if (choice.IsMember == wasMember) continue;
                sharedChanged = true;
                if (!await _library.SetSharedCategorySoundAsync(choice.GuildId, item, choice.IsMember)) sharedFailed = true;
            }
            if (sharedChanged) await RefreshSharedCategoriesAsync();
            if (sharedFailed)
                ToastService.Show(Loc.T("sounds.shared_category_failed") + " " + _library.LastErrorDetail, ToastKind.Warning);

            if (!canEdit)
            {
                if (localChanged) RefreshSections();
                return;
            }

            var newName = window.ResultName != item.Name ? window.ResultName : null;
            var newEmoji = window.ResultEmoji != (item.Emoji ?? "") ? window.ResultEmoji : null;
            var trimChanged = window.ResultTrimStartMs != item.TrimStartMs || window.ResultTrimEndMs != item.TrimEndMs;
            (int?, int?)? newTrim = trimChanged ? (window.ResultTrimStartMs, window.ResultTrimEndMs) : null;
            if (newName is null && newEmoji is null && newTrim is null)
            {
                if (localChanged) RefreshSections();
                return;
            }

            if (await _library.UpdateSoundDetailsAsync(item, newName, newEmoji, newTrim))
            {
                RefreshSections();
                ToastService.Show(Loc.T("sounds.updated", item.Name), ToastKind.Success);
            }
            else
            {
                ToastService.Show(
                    Loc.T("sounds.edit_failed") + (string.IsNullOrEmpty(_library.LastErrorDetail) ? "" : " " + _library.LastErrorDetail),
                    ToastKind.Warning);
            }
        }

        /// <summary>Peut-on ajouter/retirer ce son à la catégorie partagée de ce serveur ? Oui pour son
        /// auteur, ou pour un admin de ce serveur (même règle que le serveur, qui fait foi).</summary>
        private static bool CanShareTo(SoundLibraryService.SharedCategoryInfo? shared, SoundItem item) =>
            shared is not null && (shared.IsAdmin || item.IsMine);

        /// <summary>Retire le préfixe "🌐 " des noms de catégories partagées (ex: quand une icône Discord réelle le remplace visuellement).
        /// En chaîne plutôt qu'en char : l'emoji 🌐 occupe deux unités UTF-16 (paire de substituts) et ne tient pas dans un seul char.</summary>
        private static string StripSharedPrefix(string name) =>
            name.StartsWith("🌐 ") ? name.Substring("🌐 ".Length) : name;

        /// <summary>Retrouve la section (catégorie) qui contient physiquement ce bouton, en remontant l'arbre visuel.</summary>
        private static SectionViewModel? FindEnclosingSection(DependencyObject? element)
        {
            while (element is not null)
            {
                if (element is FrameworkElement { Tag: SectionViewModel section }) return section;
                element = VisualTreeHelper.GetParent(element);
            }
            return null;
        }
    }
}
