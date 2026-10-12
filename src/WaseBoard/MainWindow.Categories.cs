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
    // Catégories : création, renommage, import/export, partage.
    public partial class MainWindow
    {
        // ---------- Gestion des catégories ----------

        /// <summary>Glisser-déposer d'un en-tête de section pour réordonner les catégories (même
        /// logique que NavItem_Drop). Gestionnaires posés sur l'Expander (ancêtre), pas l'en-tête :
        /// le ToggleButton interne capture la souris au clic, donc "vient du header" doit être
        /// vérifié une fois au clic initial et mémorisé, jamais recalculé pendant le déplacement.</summary>
        private static bool IsDescendantOf(DependencyObject? element, DependencyObject ancestor)
        {
            while (element is not null)
            {
                if (ReferenceEquals(element, ancestor)) return true;
                element = VisualTreeHelper.GetParent(element) ?? LogicalTreeHelper.GetParent(element);
            }
            return false;
        }

        private bool _sectionDragFromHeader;
        private Border? _sectionDragOverBorder;

        private void SectionHeader_PreviewMouseLeftButtonDown(object sender, MouseButtonEventArgs e)
        {
            if (sender is not Expander { Header: Border headerBorder }) return;

            _sectionDragFromHeader = IsDescendantOf(e.OriginalSource as DependencyObject, headerBorder);
            _sectionDragStart = e.GetPosition(null);
            _isDraggingSection = false;
        }

        private void SectionHeader_PreviewMouseMove(object sender, MouseEventArgs e)
        {
            if (e.LeftButton != MouseButtonState.Pressed || _isDraggingSection || !_sectionDragFromHeader) return;
            if (sender is not Expander { DataContext: SectionViewModel section, Header: Border headerBorder } expander) return;
            if (!section.IsReorderable) return;

            var diff = _sectionDragStart - e.GetPosition(null);
            if (Math.Abs(diff.X) < SystemParameters.MinimumHorizontalDragDistance &&
                Math.Abs(diff.Y) < SystemParameters.MinimumVerticalDragDistance)
                return;

            _isDraggingSection = true;
            headerBorder.Opacity = 0.5; // indique visuellement quelle section est en cours de déplacement
            DragDrop.DoDragDrop(expander, section, DragDropEffects.Move);
            headerBorder.Opacity = 1.0;
            ClearSectionDragOverHighlight();
            _isDraggingSection = false;
        }

        private void SectionHeader_DragEnter(object sender, DragEventArgs e)
        {
            if (sender is not Border border || !e.Data.GetDataPresent(typeof(SectionViewModel))) return;
            if (ReferenceEquals(_sectionDragOverBorder, border)) return;

            ClearSectionDragOverHighlight();
            _sectionDragOverBorder = border;
            border.BorderBrush = (Brush)FindResource("AccentBrush");
            border.BorderThickness = new Thickness(2);
        }

        private void SectionHeader_DragLeave(object sender, DragEventArgs e)
        {
            if (sender is Border border && ReferenceEquals(_sectionDragOverBorder, border))
                ClearSectionDragOverHighlight();
        }

        private void ClearSectionDragOverHighlight()
        {
            if (_sectionDragOverBorder is null) return;
            _sectionDragOverBorder.BorderThickness = new Thickness(0);
            _sectionDragOverBorder = null;
        }

        private void SectionHeader_Drop(object sender, DragEventArgs e)
        {
            ClearSectionDragOverHighlight();
            if (sender is not FrameworkElement { DataContext: SectionViewModel targetSection }) return;
            if (e.Data.GetData(typeof(SectionViewModel)) is not SectionViewModel draggedSection) return;
            if (!draggedSection.IsReorderable || !targetSection.IsReorderable) return;
            if (ReferenceEquals(draggedSection, targetSection)) return;

            var order = _library.GetCategoriesInOrder(_sharedCategories.Select(s => s.GuildId));
            var oldIndex = order.IndexOf(draggedSection.CategoryKey);
            var newIndex = order.IndexOf(targetSection.CategoryKey);
            if (oldIndex < 0 || newIndex < 0) return;

            order.RemoveAt(oldIndex);
            order.Insert(newIndex, draggedSection.CategoryKey);
            _library.SaveCategoryOrder(order);
            RefreshSections();
            e.Handled = true;
        }

        /// <summary>
        /// Filet de sécurité pour toutes les sections : le binding OneTime initial d'IsExpanded ne
        /// synchronise pas toujours fiablement la hauteur visible avant que le contrôle soit
        /// chargé. Force ici la hauteur à correspondre à IsExpanded, sans animation (pas de "pop").
        /// </summary>
        private void Section_Loaded(object sender, RoutedEventArgs e)
        {
            if (sender is not Expander expander) return;

            expander.ApplyTemplate();
            if (expander.Template?.FindName("ExpandSiteBorder", expander) is Border border)
                border.MaxHeight = expander.IsExpanded ? double.PositiveInfinity : 0;
        }

        private void Section_Expanded(object sender, RoutedEventArgs e)
        {
            if (sender is not Expander expander) return;
            if (expander.Tag is string key)
            {
                _library.Settings.CollapsedSections.Remove(key);
                _library.SaveSettings();
            }
            AnimateExpanderHeight(expander, expanding: true);
        }

        private void Section_Collapsed(object sender, RoutedEventArgs e)
        {
            if (sender is not Expander expander) return;

            if (expander.Tag is string key && !_library.Settings.CollapsedSections.Contains(key))
            {
                _library.Settings.CollapsedSections.Add(key);
                _library.SaveSettings();
            }
            AnimateExpanderHeight(expander, expanding: false);
        }

        /// <summary>Anime la hauteur visible d'une section. Avant IsLoaded (binding OneTime initial,
        /// pas un clic), applique l'état directement pour éviter un "pop" à chaque RefreshSections().</summary>
        private static void AnimateExpanderHeight(Expander expander, bool expanding)
        {
            expander.ApplyTemplate();
            if (expander.Template?.FindName("ExpandSiteBorder", expander) is not Border border) return;

            if (!expander.IsLoaded)
            {
                border.BeginAnimation(FrameworkElement.MaxHeightProperty, null);
                border.MaxHeight = expanding ? double.PositiveInfinity : 0;
                return;
            }

            if (expanding)
            {
                border.BeginAnimation(FrameworkElement.MaxHeightProperty, null);
                border.MaxHeight = double.PositiveInfinity;
                border.Measure(new Size(border.ActualWidth > 0 ? border.ActualWidth : double.PositiveInfinity, double.PositiveInfinity));
                var target = border.DesiredSize.Height;
                border.MaxHeight = 0;

                var anim = new DoubleAnimation(0, target, TimeSpan.FromMilliseconds(220))
                { EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut } };
                anim.Completed += (_, _) => border.MaxHeight = double.PositiveInfinity;
                border.BeginAnimation(FrameworkElement.MaxHeightProperty, anim);
            }
            else
            {
                var current = border.ActualHeight;
                border.BeginAnimation(FrameworkElement.MaxHeightProperty, null);
                border.MaxHeight = current;

                var anim = new DoubleAnimation(current, 0, TimeSpan.FromMilliseconds(200))
                { EasingFunction = new CubicEase { EasingMode = EasingMode.EaseIn } };
                border.BeginAnimation(FrameworkElement.MaxHeightProperty, anim);
            }
        }

        /// <summary>Menu déroulant (Nouvelle catégorie / Importer) plutôt qu'un second bouton dans une
        /// barre d'outils déjà tassée sur une seule ligne.</summary>
        private void AddCategoryButton_Click(object sender, RoutedEventArgs e)
        {
            var menu = new ContextMenu { PlacementTarget = AddCategoryButton, Placement = PlacementMode.Bottom };

            var newItem = new MenuItem { Header = "Nouvelle catégorie..." };
            newItem.Click += (_, _) => CreateCategoryPrompt();
            menu.Items.Add(newItem);

            var importItem = new MenuItem { Header = "📥 Importer une catégorie..." };
            importItem.Click += async (_, _) => await ImportCategoryAsync();
            menu.Items.Add(importItem);

            menu.IsOpen = true;
        }

        private void CreateCategoryPrompt()
        {
            var name = PromptDialog.Show(this, "Nom de la nouvelle catégorie :", "");
            if (string.IsNullOrWhiteSpace(name)) return;
            _library.CreateCategory(name.Trim());
            RefreshSections();
        }

        /// <summary>Format du fichier d'ExportCategory/ImportCategoryAsync. Le nom est conservé en
        /// plus de l'id, pour retrouver un son par nom si importé sur un autre serveur.</summary>
        private class CategoryExport
        {
            public string Category { get; set; } = "";
            public List<CategoryExportSound> Sounds { get; set; } = new();
        }

        private class CategoryExportSound
        {
            public string Id { get; set; } = "";
            public string Name { get; set; } = "";
        }

        /// <summary>Clic droit sur la barre d'une section : l'export est proposé pour toutes (favoris, catégorie
        /// perso/partagée, tous les sons) ; renommer et supprimer seulement pour les catégories personnelles.</summary>
        private void SectionHeader_RightClick(object sender, MouseButtonEventArgs e)
        {
            if (sender is not FrameworkElement { Tag: SectionViewModel section }) return;

            var menu = new ContextMenu();
            var canManage = section.IsManageable && !section.IsShared;

            if (canManage)
            {
                var renameItem = new MenuItem { Header = "✎ Renommer..." };
                renameItem.Click += (_, _) => RenameCategory(section);
                menu.Items.Add(renameItem);
            }

            var exportItem = new MenuItem { Header = "⬇ Exporter cette catégorie..." };
            exportItem.Click += (_, _) => ExportCategory(section);
            menu.Items.Add(exportItem);

            if (canManage)
            {
                menu.Items.Add(new Separator());
                var deleteItem = new MenuItem { Header = "🗑 Supprimer la catégorie..." };
                deleteItem.Click += (_, _) => DeleteCategory(section);
                menu.Items.Add(deleteItem);
            }
            menu.IsOpen = true;
        }

        /// <summary>Exporte n'importe quelle section (favoris, catégorie perso/partagée, tous les sons)
        /// dans un fichier JSON partageable — action non destructive, disponible pour toutes,
        /// contrairement à renommer/supprimer qui restent réservés aux catégories personnelles.</summary>
        private void ExportCategory(SectionViewModel section)
        {
            var dialog = new SaveFileDialog
            {
                Title = "Exporter la catégorie",
                Filter = "Catégorie WaseBoard (*.wbcat.json)|*.wbcat.json",
                FileName = SanitizeFileName(StripSharedPrefix(section.Name)) + ".wbcat.json"
            };
            if (dialog.ShowDialog(this) != true) return;

            var export = new CategoryExport
            {
                Category = StripSharedPrefix(section.Name),
                Sounds = section.Sounds.Select(s => new CategoryExportSound { Id = s.Id, Name = s.Name }).ToList()
            };

            try
            {
                File.WriteAllText(dialog.FileName, JsonSerializer.Serialize(export, new JsonSerializerOptions { WriteIndented = true }));
                ToastService.Show($"Catégorie « {export.Category} » exportée ({export.Sounds.Count} son(s)).", ToastKind.Success);
            }
            catch (Exception ex)
            {
                ToastService.Show("Échec de l'export : " + ex.Message, ToastKind.Error);
            }
        }

        private static string SanitizeFileName(string name)
        {
            var invalid = Path.GetInvalidFileNameChars();
            var cleaned = new string(name.Where(c => !invalid.Contains(c)).ToArray()).Trim();
            return string.IsNullOrEmpty(cleaned) ? "categorie" : cleaned;
        }

        /// <summary>Importe une catégorie exportée : sons retrouvés par id puis par nom en repli,
        /// ceux introuvables sur ce serveur sont ignorés et comptés dans le résumé.</summary>
        private async Task ImportCategoryAsync()
        {
            var dialog = new OpenFileDialog
            {
                Title = "Importer une catégorie",
                Filter = "Catégorie WaseBoard (*.wbcat.json)|*.wbcat.json|Tous les fichiers|*.*"
            };
            if (dialog.ShowDialog(this) != true) return;

            CategoryExport? import;
            try
            {
                var json = await File.ReadAllTextAsync(dialog.FileName);
                import = JsonSerializer.Deserialize<CategoryExport>(json);
            }
            catch (Exception ex)
            {
                ToastService.Show("Fichier de catégorie invalide : " + ex.Message, ToastKind.Error);
                return;
            }

            if (import is null || import.Sounds.Count == 0)
            {
                ToastService.Show("Ce fichier ne contient aucun son.", ToastKind.Warning);
                return;
            }

            var suggestedName = string.IsNullOrWhiteSpace(import.Category) ? "Catégorie importée" : import.Category;
            var name = PromptDialog.Show(this, "Nom de la catégorie importée :", suggestedName);
            if (string.IsNullOrWhiteSpace(name)) return;
            var trimmedName = name.Trim();

            _library.CreateCategory(trimmedName);

            var found = 0;
            foreach (var soundRef in import.Sounds)
            {
                var match = Sounds.FirstOrDefault(s => s.Id == soundRef.Id)
                            ?? Sounds.FirstOrDefault(s => string.Equals(s.Name, soundRef.Name, StringComparison.OrdinalIgnoreCase));
                if (match is null) continue;
                _library.AddSoundToCategory(trimmedName, match);
                found++;
            }

            RefreshSections();

            var skipped = import.Sounds.Count - found;
            ToastService.Show(
                skipped > 0
                    ? $"Catégorie « {trimmedName} » importée : {found} son(s) ajouté(s), {skipped} introuvable(s) sur ce serveur."
                    : $"Catégorie « {trimmedName} » importée : {found} son(s) ajouté(s).",
                skipped > 0 ? ToastKind.Warning : ToastKind.Success);
        }

        // Catégories partagées : créées automatiquement par serveur Discord (voir
        // RefreshSharedCategoriesAsync), non renommables/supprimables — rename/delete ci-dessous
        // ne concernent que les catégories personnelles (menu clic droit de leur en-tête).

        private void RenameCategory(SectionViewModel section)
        {
            if (section.IsShared) return;
            var newName = PromptDialog.Show(this, "Nouveau nom de catégorie :", section.Name);
            if (string.IsNullOrWhiteSpace(newName)) return;

            _library.RenameCategory(section.CategoryKey, newName.Trim());
            RefreshSections();
        }

        private void DeleteCategory(SectionViewModel section)
        {
            if (section.IsShared) return;

            var confirm = ConfirmDialog.Show(this, $"Supprimer la catégorie « {section.Name} » ?");
            if (!confirm) return;

            _library.DeleteCategory(section.CategoryKey);
            RefreshSections();
        }
    }
}
