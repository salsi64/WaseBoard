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
    // Thème, palette, barre d'outils : couleurs de l'application et densité de la barre d'outils.
    public partial class MainWindow
    {
        /// <summary>Applique la couleur de fond personnalisée (si définie) à toute l'application, immédiatement.</summary>
        private void ApplyBackgroundColor()
        {
            if (string.IsNullOrWhiteSpace(_library.Settings.BackgroundColorHex)) return;
            try
            {
                var color = (Color)ColorConverter.ConvertFromString(_library.Settings.BackgroundColorHex);
                var res = Application.Current.Resources;
                res["BgBrush"] = new SolidColorBrush(color);
                // Panneaux et pistes dérivés du fond choisi, pour rester cohérents avec lui
                // (sinon ils garderaient les couleurs de la palette, d'une autre teinte).
                res["PanelBrush"] = new SolidColorBrush(PalettePresets.Lighten(color, 0.06));
                res["TrackBrush"] = new SolidColorBrush(PalettePresets.Lighten(color, 0.14));
            }
            catch { /* couleur invalide enregistrée : on garde le thème par défaut */ }
        }

        /// <summary>Ne réapplique le thème système que s'il a réellement changé depuis le dernier sondage.</summary>
        private void ApplyColorSchemeIfSystemChanged()
        {
            if (!_library.Settings.FollowSystemTheme && !_library.Settings.FollowSystemAccent) return;
            var current = RegistryThemeWatcher.ReadCurrent();
            if (_lastSystemTheme is not null &&
                _lastSystemTheme.IsLightTheme == current.IsLightTheme &&
                _lastSystemTheme.AccentColor == current.AccentColor) return;
            ApplyColorScheme();
        }

        /// <summary>
        /// Applique la palette choisie (Paramètres > Apparence), à chaud (brushes DynamicResource) :
        /// clair/sombre suit Windows si « suivre le thème » est coché, l'accent suit Windows si
        /// « suivre l'accent » est coché, et une couleur de fond personnalisée remplace le fond de la
        /// palette quand le thème système n'est pas suivi.
        /// </summary>
        private void ApplyColorScheme()
        {
            var settings = _library.Settings;
            var preset = PalettePresets.Get(settings.PaletteId);

            var snapshot = settings.FollowSystemTheme || settings.FollowSystemAccent ? RegistryThemeWatcher.ReadCurrent() : null;
            _lastSystemTheme = snapshot;

            var isLight = settings.FollowSystemTheme && snapshot is { IsLightTheme: true };
            var accent = settings.FollowSystemAccent && snapshot is not null ? snapshot.AccentColor : preset.Accent;
            ApplyPalette(preset, isLight, accent);

            if (!settings.FollowSystemTheme) ApplyBackgroundColor();

            // Après le fond personnalisé : les jetons se calculent depuis les brosses FINALES.
            ApplyThemeTokens(settings.UiTheme == "Flat");
        }

        /// <summary>Jetons de style : ce qui sépare le thème Flat des autres, indépendamment de la palette. Flat = champs « en
        /// creux » (plus sombres que la fenêtre), fenêtres secondaires sur le panneau, boutons principaux en dégradé
        /// d'accent, bordures douces, curseurs fins, liseré discret sur les pills. Classique/Moderne gardent leur rendu.</summary>
        private static void ApplyThemeTokens(bool flat)
        {
            var res = Application.Current.Resources;
            var bg = (Brush)res["BgBrush"];
            var panel = (Brush)res["PanelBrush"];
            var track = (Brush)res["TrackBrush"];
            var text = (Brush)res["TextBrush"];
            var accent = (Brush)res["AccentBrush"];
            var gradient = (Brush)res["AccentGradientBrush"];

            res["PrimaryBrush"] = flat ? gradient : accent;
            res["FieldBrush"] = flat ? bg : panel;
            res["OutlineBrush"] = flat ? track : text;
            res["SurfaceBrush"] = flat ? panel : bg;
            res["CardBrush"] = flat ? bg : panel;
            res["SecondaryBrush"] = flat ? bg : Brushes.Transparent;
            res["PillBorderBrush"] = flat ? track : Brushes.Transparent;
            res["NavOutlineBrush"] = flat ? track : new SolidColorBrush(Color.FromRgb(0x70, 0x70, 0x70));
            res["SliderThumbFill"] = flat ? gradient : Brushes.White;
            res["SliderThumbStroke"] = flat ? panel : accent;
            res["FieldRadius"] = new CornerRadius(flat ? 10 : 8);
            res["SecondaryBorderThickness"] = new Thickness(flat ? 1 : 0);
            res["SliderTrackHeight"] = flat ? 4.0 : 6.0;
        }

        private static void ApplyPalette(PalettePreset preset, bool isLight, Color accent)
        {
            var res = Application.Current.Resources;
            res["BgBrush"] = new SolidColorBrush(isLight ? PalettePresets.LightBg : preset.Bg);
            res["PanelBrush"] = new SolidColorBrush(isLight ? PalettePresets.LightPanel : preset.Panel);
            res["TextBrush"] = new SolidColorBrush(isLight ? PalettePresets.LightText : preset.Text);
            res["TrackBrush"] = new SolidColorBrush(isLight ? PalettePresets.LightTrack : preset.Track);
            res["AccentBrush"] = new SolidColorBrush(accent);
            res["OnAccentBrush"] = new SolidColorBrush(PalettePresets.OnAccent(accent));

            // Dégradé « accent → teinte voisine » (boutons principaux, vignette, poignées de la fenêtre d'édition).
            var accent2 = PalettePresets.ShiftHue(accent, -28);
            res["Accent2Brush"] = new SolidColorBrush(accent2);
            res["AccentGradientBrush"] = new LinearGradientBrush(accent, accent2, new Point(0, 0), new Point(1, 1));
        }

        /// <summary>Bascule thème classique/moderne/flat (flat = disposition moderne + style d'interface Flat ; la
        /// palette de couleurs reste un choix indépendant). Paramètres/volume de la barre d'outils sont masqués en
        /// moderne (déjà dans la barre latérale), seule voie d'accès en classique.</summary>
        private void ApplyTheme()
        {
            var isModern = _library.Settings.UiTheme is "Modern" or "Flat";
            ThemeState.IsModern = isModern;
            ThemeState.Options.ShowWaveforms = _library.Settings.ShowWaveforms;

            Sidebar.Visibility = isModern ? Visibility.Visible : Visibility.Collapsed;
            SidebarColumn.Width = new GridLength(isModern ? (_sidebarCollapsed ? 60 : 230) : 0);
            TitleText.Visibility = isModern ? Visibility.Collapsed : Visibility.Visible;
            SettingsButton.Visibility = isModern ? Visibility.Collapsed : Visibility.Visible;

            // Thème moderne : « En ce moment » et le statut serveur vivent dans la barre latérale, la
            // barre du haut (qui ne servait qu'à ça) disparaît et rend ses 40 px aux sons.
            TopStrip.Visibility = isModern ? Visibility.Collapsed : Visibility.Visible;
            PlaceStatusPanel(isModern);
            ApplyToolbarDensity();

            // Les boutons de son utilisent un ItemTemplateSelector qui lit ThemeState.IsModern :
            // il faut reconstruire les sections pour que le changement de gabarit soit pris en compte.
            RefreshSections();
        }

        /// <summary>Rattache le statut serveur (point + « Connecté — N sons ») à la barre latérale en
        /// thème moderne, ou à la barre du haut en classique : un seul jeu d'éléments, déplacé, plutôt
        /// que deux copies à tenir synchronisées.</summary>
        private void PlaceStatusPanel(bool isModern)
        {
            Panel target = isModern ? SidebarStatusHost : StripStatusHost;
            if (!ReferenceEquals(StatusPanel.Parent, target))
            {
                (StatusPanel.Parent as Panel)?.Children.Remove(StatusPanel);
                target.Children.Add(StatusPanel);
            }
            StatusPanel.HorizontalAlignment = isModern ? HorizontalAlignment.Left : HorizontalAlignment.Right;
        }

        private void Window_SizeChanged(object sender, SizeChangedEventArgs e) => ApplyToolbarDensity();

        /// <summary>Barre d'outils en version étroite (libellés masqués) — voir ApplyToolbarDensity.</summary>
        private bool _toolbarCompact;

        /// <summary>Largeur que la recherche garde tant que la barre peut encore être raccourcie : quand la
        /// fenêtre rétrécit, la recherche rétrécit d'abord jusque-là, puis les libellés se raccourcissent.</summary>
        private const double SearchComfortWidth = 150;

        /// <summary>Paliers de la barre d'outils, du plus complet au plus compact (voir SetToolbarDensity).</summary>
        private const int ToolbarDensityLevels = 4;

        /// <summary>Choisit le palier le plus complet de la barre d'outils qui laisse encore à la recherche
        /// sa largeur de confort, pour que les boutons ne sortent jamais de la fenêtre. Les largeurs sont
        /// MESURÉES (et non devinées par des seuils fixes) : elles dépendent du thème (titre, Paramètres), des
        /// boutons présents (interrupteur Micro en jeu) et du mode affiché.</summary>
        private void ApplyToolbarDensity()
        {
            if (JoinVoiceLabel is null) return; // encore en cours d'InitializeComponent

            var sidebarWidth = ThemeState.IsModern ? SidebarColumn.Width.Value : 0;
            var available = ActualWidth - sidebarWidth - 24;
            var unbounded = new Size(double.PositiveInfinity, double.PositiveInfinity);

            for (var level = 0; level < ToolbarDensityLevels; level++)
            {
                SetToolbarDensity(level);
                if (level == ToolbarDensityLevels - 1) break; // le plus compact, faute de mieux

                // Un libellé masqué ou raccourci n'invalide la taille de son bouton qu'à la prochaine passe
                // de mise en page : sans ça, la mesure renverrait la largeur du palier PRÉCÉDENT. On
                // invalide donc juste la chaîne libellé → barre (pas toute la fenêtre, coûteux en
                // redimensionnement avec un gros catalogue).
                foreach (var label in new FrameworkElement[] { JoinVoiceLabel, StopAllLabel, AddCategoryLabel, AddSoundLabel, MicModeGameLabel, MicModeDiscordLabel })
                    InvalidateMeasureUpTo(label, ToolbarActions);

                ToolbarActions.Measure(unbounded);
                TitleText.Measure(unbounded);
                var titleWidth = TitleText.Visibility == Visibility.Visible ? TitleText.DesiredSize.Width : 0;
                var needed = titleWidth + ToolbarActions.DesiredSize.Width + 12; // + marge à droite de la recherche
                if (available - needed >= SearchComfortWidth) break;
            }
        }

        private static void InvalidateMeasureUpTo(DependencyObject element, UIElement root)
        {
            for (var current = element; current is not null; current = VisualTreeHelper.GetParent(current))
            {
                (current as UIElement)?.InvalidateMeasure();
                if (ReferenceEquals(current, root)) break;
            }
        }

        /// <summary>0 : tout ; 1 : sans le titre (thème classique — déjà dans la barre de titre de la
        /// fenêtre) ; 2 : « Catégorie » en icône seule et « Son » au lieu d'« Ajouter un son » ;
        /// 3 : icônes seules.</summary>
        private void SetToolbarDensity(int level)
        {
            TitleText.Visibility = !ThemeState.IsModern && level == 0 ? Visibility.Visible : Visibility.Collapsed;
            AddCategoryLabel.Visibility = level < 2 ? Visibility.Visible : Visibility.Collapsed;
            AddSoundLabel.Text = level < 2 ? "Ajouter un son" : "Son";
            var labels = level < 3 ? Visibility.Visible : Visibility.Collapsed;
            JoinVoiceLabel.Visibility = labels;
            StopAllLabel.Visibility = labels;
            _toolbarCompact = level >= 3;
            UpdateMicModeLabels();
        }
    }
}
