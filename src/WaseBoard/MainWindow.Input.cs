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

namespace WaseBoard
{
    // Paramètres, dépôt de fichiers, palette Ctrl+K et raccourcis clavier globaux.
    public partial class MainWindow
    {
        // ---------- Paramètres ----------

        private async void SettingsButton_Click(object sender, RoutedEventArgs e)
        {
            // Le panel d'administration s'ouvre depuis les Paramètres, et n'y apparaît que si l'on
            // administre au moins un serveur (le serveur re-vérifie de toute façon chaque requête).
            var adminGuilds = _sharedCategories.Where(s => s.IsAdmin).ToList();
            var window = new SettingsWindow(_library.Settings, _library, adminGuilds: adminGuilds, knownSounds: Sounds, micFeed: _micFeed) { Owner = this };
            var saved = window.ShowDialog() == true;
            if (saved)
            {
                _library.SaveSettings();
                ApplyColorScheme();
                ApplyTheme();
                ApplyMicModeButton();
            }

            // « Se déconnecter » agit tout de suite (sans passer par « Enregistrer ») : plus de session, donc plus de
            // catalogue à rafraîchir — on le dit à l'écran au lieu d'enchaîner des appels qui échoueraient.
            if (window.LoggedOut)
            {
                HandleLoggedOut();
                return;
            }

            if (saved)
            {
                await RefreshCatalogAsync();
                await RefreshSharedCategoriesAsync();
                await RefreshVoiceStatusAsync();
            }
            else if (window.AdminCatalogChanged)
            {
                // Sons renommés/supprimés/restaurés depuis le panel d'administration, sans avoir
                // enregistré les Paramètres : le catalogue affiché doit quand même être rechargé.
                await RefreshCatalogAsync();
                await RefreshSharedCategoriesAsync();
            }
        }

        // ---------- Glisser-déposer de fichiers (ajout de sons) ----------

        private void Window_DragEnter(object sender, DragEventArgs e)
        {
            if (e.Data.GetDataPresent(DataFormats.FileDrop))
            {
                e.Effects = DragDropEffects.Copy;
                DropOverlay.Visibility = Visibility.Visible;
            }
            else
            {
                e.Effects = DragDropEffects.None;
            }
        }

        private void Window_DragLeave(object sender, DragEventArgs e) => DropOverlay.Visibility = Visibility.Collapsed;

        private async void Window_Drop(object sender, DragEventArgs e)
        {
            DropOverlay.Visibility = Visibility.Collapsed;

            if (!e.Data.GetDataPresent(DataFormats.FileDrop)) return;

            if (e.Data.GetData(DataFormats.FileDrop) is string[] filePaths)
                await AddSoundFiles(filePaths);
        }

        // ---------- Palette de commande (Ctrl+K) ----------

        private void MainWindow_PreviewKeyDown(object sender, KeyEventArgs e)
        {
            if (e.Key == Key.K && Keyboard.Modifiers == ModifierKeys.Control)
            {
                e.Handled = true;
                OpenCommandPalette();
            }
        }

        private void OpenCommandPalette()
        {
            CommandPaletteOverlay.Visibility = Visibility.Visible;
            CommandPaletteBox.Text = "";
            CommandPaletteList.ItemsSource = Sounds;
            CommandPaletteBox.Focus();
        }

        private void CloseCommandPalette() => CommandPaletteOverlay.Visibility = Visibility.Collapsed;

        private void CommandPaletteOverlay_MouseLeftButtonDown(object sender, MouseButtonEventArgs e) => CloseCommandPalette();

        private void CommandPalettePanel_MouseLeftButtonDown(object sender, MouseButtonEventArgs e) => e.Handled = true;

        private void CommandPaletteBox_TextChanged(object sender, TextChangedEventArgs e)
        {
            var query = CommandPaletteBox.Text;
            CommandPaletteList.ItemsSource = string.IsNullOrWhiteSpace(query)
                ? Sounds
                : Sounds.Where(s => s.Name.Contains(query, StringComparison.OrdinalIgnoreCase)).ToList();
            if (CommandPaletteList.Items.Count > 0) CommandPaletteList.SelectedIndex = 0;
        }

        private void CommandPaletteBox_PreviewKeyDown(object sender, KeyEventArgs e)
        {
            switch (e.Key)
            {
                case Key.Escape:
                    e.Handled = true;
                    CloseCommandPalette();
                    break;
                case Key.Down:
                    e.Handled = true;
                    if (CommandPaletteList.SelectedIndex < CommandPaletteList.Items.Count - 1) CommandPaletteList.SelectedIndex++;
                    CommandPaletteList.ScrollIntoView(CommandPaletteList.SelectedItem);
                    break;
                case Key.Up:
                    e.Handled = true;
                    if (CommandPaletteList.SelectedIndex > 0) CommandPaletteList.SelectedIndex--;
                    CommandPaletteList.ScrollIntoView(CommandPaletteList.SelectedItem);
                    break;
                case Key.Enter:
                    e.Handled = true;
                    PlaySelectedFromCommandPalette();
                    break;
            }
        }

        private void CommandPaletteList_MouseDoubleClick(object sender, MouseButtonEventArgs e) => PlaySelectedFromCommandPalette();

        private void PlaySelectedFromCommandPalette()
        {
            var item = CommandPaletteList.SelectedItem as SoundItem
                ?? CommandPaletteList.Items.Cast<SoundItem>().FirstOrDefault();
            if (item is null) return;
            CloseCommandPalette();
            Play(item);
        }

        // ---------- Raccourcis clavier globaux ----------

        private void RegisterAllHotkeys()
        {
            foreach (var item in Sounds)
                RegisterHotkeyFor(item);
            RegisterMicModeHotkey();
        }

        private void RegisterHotkeyFor(SoundItem item)
        {
            if (string.IsNullOrEmpty(item.Hotkey) || _hotkeys is null) return;

            if (HotkeyParser.TryParse(item.Hotkey, out var modifiers, out var key))
                _hotkeys.Register(modifiers, key, () => Dispatcher.Invoke(() => Play(item)));
        }

        /// <summary>Reconstruit tous les raccourcis depuis Sounds/Settings.SoundHotkeys — le
        /// GlobalHotkeyManager n'expose pas de désenregistrement ciblé par son, donc toute
        /// modification (retrait, suppression du son) repasse par un cycle complet unregister/register.</summary>
        private void ResyncHotkeys()
        {
            _hotkeys?.UnregisterAll();
            RegisterAllHotkeys();
        }

        private void AssignHotkey(SoundItem item)
        {
            var captureWindow = new HotkeyCaptureWindow { Owner = this };
            if (captureWindow.ShowDialog() == true && captureWindow.CapturedHotkey is not null)
            {
                _library.SetHotkey(item, captureWindow.CapturedHotkey);
                _hotkeys?.UnregisterAll();
                RegisterAllHotkeys();
            }
        }
    }
}
