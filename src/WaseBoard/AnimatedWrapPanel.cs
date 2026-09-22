using System;
using System.Collections.Generic;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Animation;

namespace WaseBoard
{
    /// <summary>
    /// Un WrapPanel qui anime le déplacement de ses enfants quand leur position change (technique
    /// "FLIP" : on capture la position avant/après, et on anime la différence). Utilisé pour les
    /// grilles de boutons de son, afin que réordonner un bouton fasse visiblement "s'écarter" les
    /// autres au lieu de sauter brutalement à leur nouvelle place.
    ///
    /// Fonctionne pour n'importe quel changement de position (réordonnancement en direct pendant un
    /// glisser-déposer, ajout/suppression d'un élément, filtrage par recherche, etc.), tant que les
    /// mêmes instances d'éléments visuels persistent entre les deux dispositions — WPF ne réutilise
    /// les conteneurs que si la collection source notifie des changements incrémentaux (Move/Add/
    /// Remove) plutôt que d'être entièrement remplacée.
    /// </summary>
    public class AnimatedWrapPanel : WrapPanel
    {
        private static readonly TimeSpan AnimationDuration = TimeSpan.FromMilliseconds(380);

        protected override Size ArrangeOverride(Size finalSize)
        {
            // Position de chaque enfant AVANT ce passage d'arrangement (donc sa position actuelle,
            // héritée du passage précédent).
            var previousPositions = new Dictionary<UIElement, Rect>();
            foreach (UIElement child in InternalChildren)
            {
                if (child.IsArrangeValid || child.RenderSize.Width > 0)
                {
                    var transform = child.TransformToAncestor(this);
                    previousPositions[child] = transform.TransformBounds(new Rect(child.RenderSize));
                }
            }

            var result = base.ArrangeOverride(finalSize);

            foreach (UIElement child in InternalChildren)
            {
                if (!previousPositions.TryGetValue(child, out var oldBounds)) continue;

                var newTransform = child.TransformToAncestor(this);
                var newBounds = newTransform.TransformBounds(new Rect(child.RenderSize));

                var deltaX = oldBounds.X - newBounds.X;
                var deltaY = oldBounds.Y - newBounds.Y;

                if (Math.Abs(deltaX) > 0.5 || Math.Abs(deltaY) > 0.5)
                    AnimateChildMove(child, deltaX, deltaY);
            }

            return result;
        }

        private static void AnimateChildMove(UIElement child, double fromX, double fromY)
        {
            if (child.RenderTransform is not TranslateTransform transform || transform.IsFrozen)
            {
                transform = new TranslateTransform();
                child.RenderTransform = transform;
            }

            var ease = new CubicEase { EasingMode = EasingMode.EaseOut };
            transform.BeginAnimation(TranslateTransform.XProperty,
                new DoubleAnimation(fromX, 0, AnimationDuration) { EasingFunction = ease });
            transform.BeginAnimation(TranslateTransform.YProperty,
                new DoubleAnimation(fromY, 0, AnimationDuration) { EasingFunction = ease });
        }
    }
}
