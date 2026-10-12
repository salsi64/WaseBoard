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
    // Réorganisation et classement des sons par glisser-déposer.
    public partial class MainWindow
    {
        // ---------- Réorganisation / classement par glisser-déposer ----------

        private Button? _dragOverButton;
        private Border? _dragVisual;

        private void SoundButton_PreviewMouseLeftButtonDown(object sender, MouseButtonEventArgs e)
        {
            _dragStartPoint = e.GetPosition(null);
            _isDragging = false;
        }

        private void SoundButton_PreviewMouseMove(object sender, MouseEventArgs e)
        {
            if (e.LeftButton != MouseButtonState.Pressed || _isDragging) return;
            if (sender is not Button { Tag: SoundItem item } button) return;

            var diff = _dragStartPoint - e.GetPosition(null);
            if (Math.Abs(diff.X) < SystemParameters.MinimumHorizontalDragDistance &&
                Math.Abs(diff.Y) < SystemParameters.MinimumVerticalDragDistance)
                return;

            _isDragging = true;
            StartWiggle(button);
            ShowDragVisual(button, e.GetPosition(DragVisualCanvas));
            DragDrop.DoDragDrop(button, item, DragDropEffects.Move);
            HideDragVisual();
            StopWiggle(button);
            ClearDragOverHighlight();
            _isDragging = false;
        }

        /// <summary>Copie semi-transparente du bouton, suit le curseur pendant le glisser (repositionnée
        /// à chaque Window_PreviewDragOver) ; le classement réel s'applique au dépôt.</summary>
        private void ShowDragVisual(Button source, Point startPosition)
        {
            _dragVisual = new Border
            {
                Width = source.ActualWidth,
                Height = source.ActualHeight,
                Background = new VisualBrush(source) { Stretch = Stretch.None },
                Opacity = 0.8,
                IsHitTestVisible = false,
                Effect = new DropShadowEffect { Color = Colors.Black, BlurRadius = 14, ShadowDepth = 2, Opacity = 0.5 }
            };
            DragVisualCanvas.Children.Add(_dragVisual);
            PositionDragVisual(startPosition);
        }

        private void PositionDragVisual(Point position)
        {
            if (_dragVisual is null) return;
            Canvas.SetLeft(_dragVisual, position.X - _dragVisual.Width / 2);
            Canvas.SetTop(_dragVisual, position.Y - _dragVisual.Height / 2);
        }

        private void HideDragVisual()
        {
            if (_dragVisual is null) return;
            DragVisualCanvas.Children.Remove(_dragVisual);
            _dragVisual = null;
        }

        /// <summary>Fait suivre le calque flottant (ShowDragVisual) au curseur pendant tout glisser-déposer
        /// en cours dans la fenêtre — tunnel depuis la racine, donc reçu même si un contrôle enfant
        /// (bouton, section...) gère aussi son propre DragOver.</summary>
        private void Window_PreviewDragOver(object sender, DragEventArgs e)
        {
            if (_dragVisual is not null)
                PositionDragVisual(e.GetPosition(DragVisualCanvas));
        }

        /// <summary>Petit balancement (façon icônes iOS en réorganisation) sur le bouton en cours de déplacement.</summary>
        private static void StartWiggle(Button button)
        {
            var transform = new RotateTransform(0);
            button.RenderTransform = transform;
            button.RenderTransformOrigin = new Point(0.5, 0.5);

            var animation = new DoubleAnimation
            {
                From = -3,
                To = 3,
                Duration = TimeSpan.FromMilliseconds(110),
                AutoReverse = true,
                RepeatBehavior = RepeatBehavior.Forever
            };
            transform.BeginAnimation(RotateTransform.AngleProperty, animation);
        }

        private static void StopWiggle(Button button)
        {
            if (button.RenderTransform is RotateTransform transform)
                transform.BeginAnimation(RotateTransform.AngleProperty, null);
            button.RenderTransform = Transform.Identity;
        }

        /// <summary>Met en valeur le bouton survolé et réordonne en direct pendant le survol (pas
        /// seulement au dépôt), pour déclencher l'animation d'écartement d'AnimatedWrapPanel.</summary>
        private DateTime _lastLiveReorder = DateTime.MinValue;
        private static readonly TimeSpan LiveReorderThrottle = TimeSpan.FromMilliseconds(220);

        private void SoundButton_DragOver(object sender, DragEventArgs e)
        {
            if (sender is not Button { Tag: SoundItem targetItem } button) return;
            if (e.Data.GetData(typeof(SoundItem)) is not SoundItem draggedItem) return;

            if (!ReferenceEquals(_dragOverButton, button))
            {
                ClearDragOverHighlight();
                _dragOverButton = button;
                button.Effect = new DropShadowEffect { Color = Colors.White, BlurRadius = 16, ShadowDepth = 0, Opacity = 0.9 };
            }

            if (ReferenceEquals(draggedItem, targetItem)) return;

            // « Récents » est classée par date de lecture : on n'y réordonne rien (ni son ordre propre, ni l'ordre général).
            if (FindEnclosingSection(button)?.CategoryKey == RecentsKey) return;

            // Espace les réordonnancements en direct d'au moins ~220ms pour éviter une animation
            // nerveuse ; le highlight de la cible, lui, reste instantané.
            if (DateTime.Now - _lastLiveReorder < LiveReorderThrottle) return;
            _lastLiveReorder = DateTime.Now;

            // Collection maîtresse (l'ordre qui sera persisté) : toujours réordonnée, quelle que
            // soit la section survolée, puisque c'est elle qui définit l'ordre global.
            var masterOld = Sounds.IndexOf(draggedItem);
            var masterNew = Sounds.IndexOf(targetItem);
            if (masterOld >= 0 && masterNew >= 0 && masterOld != masterNew)
                Sounds.Move(masterOld, masterNew);

            // Et la section affichée elle-même, si le son en fait déjà partie (sinon c'est un
            // classement, géré au dépôt par ApplySectionMembership).
            var section = _currentSections.FirstOrDefault(s => s.Sounds.Contains(draggedItem) && s.Sounds.Contains(targetItem));
            if (section is not null)
            {
                var sectionOld = section.Sounds.IndexOf(draggedItem);
                var sectionNew = section.Sounds.IndexOf(targetItem);
                if (sectionOld >= 0 && sectionNew >= 0 && sectionOld != sectionNew)
                    section.Sounds.Move(sectionOld, sectionNew);
            }
        }

        private void SoundButton_DragLeave(object sender, DragEventArgs e)
        {
            if (sender is Button button && ReferenceEquals(_dragOverButton, button))
                ClearDragOverHighlight();
        }

        private void ClearDragOverHighlight()
        {
            // ClearValue (pas "Effect = null") : laisse le déclencheur de style du highlight de
            // lecture reprendre la main, plutôt que de bloquer sur une valeur locale.
            _dragOverButton?.ClearValue(UIElement.EffectProperty);
            _dragOverButton = null;
        }

        private async void Section_Drop(object sender, DragEventArgs e)
        {
            if (sender is not FrameworkElement { Tag: SectionViewModel section }) return;
            if (e.Data.GetData(typeof(SoundItem)) is not SoundItem draggedItem) return;

            // On ne dépose rien dans « Récents » : elle se remplit toute seule.
            if (section.CategoryKey == RecentsKey)
            {
                ClearDragOverHighlight();
                e.Handled = true;
                return;
            }

            // Déjà membre ? Le survol a déjà tout réordonné (SoundButton_DragOver) ; reconstruire
            // casserait l'animation. Seul un nouveau classement nécessite RefreshSections().
            var wasAlreadyMember = section.Sounds.Contains(draggedItem);

            // Déjà membre : aucun classement à refaire (et pas de vérification de droits à déclencher
            // juste pour avoir réordonné un son dans sa propre catégorie).
            if (!wasAlreadyMember) await ApplySectionMembership(section, draggedItem);
            _library.SaveSoundOrder(Sounds);
            ClearDragOverHighlight();
            e.Handled = true;

            if (!wasAlreadyMember) RefreshSections();
        }

        private async Task ApplySectionMembership(SectionViewModel section, SoundItem item)
        {
            if (section.CategoryKey == FavoritesKey)
            {
                if (!item.IsFavorite) _library.ToggleFavorite(item);
            }
            else if (section.IsShared)
            {
                if (!CanShareTo(_sharedCategories.FirstOrDefault(s => s.GuildId == section.CategoryKey), item))
                {
                    ToastService.Show(
                        "Seuls l'auteur du son ou un administrateur du serveur peuvent l'ajouter à cette catégorie.",
                        ToastKind.Warning);
                    return;
                }

                if (!await _library.SetSharedCategorySoundAsync(section.CategoryKey, item, true))
                    ToastService.Show("Ajout à la catégorie impossible. " + _library.LastErrorDetail, ToastKind.Warning);
                _sharedCategories = await _library.FetchSharedCategoriesAsync();
            }
            else
            {
                _library.AddSoundToCategory(section.CategoryKey, item);
            }
        }
    }
}
