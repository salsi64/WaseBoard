using System;
using System.Collections.Generic;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Animation;

namespace WaseBoard
{
    /// <summary>
    /// WrapPanel qui anime le déplacement de ses enfants quand leur position change (technique
    /// FLIP : capture la position avant/après, anime la différence). Requiert des
    /// ObservableCollection modifiées in-place (Move/Add/Remove), pas reconstruites.
    /// </summary>
    public class AnimatedWrapPanel : WrapPanel
    {
        private static readonly TimeSpan AnimationDuration = TimeSpan.FromMilliseconds(380);

        /// <summary>
        /// Cellules à largeur fixe en thème moderne : les pilules ont une largeur organique qui
        /// varie avec leur contenu, ce qui décalerait les boutons suivants dès qu'un seul change
        /// (ex: avatar de qui joue le son). Le contenu peut grandir sans affecter ses voisins,
        /// l'excédent étant rogné (ClipToBounds). Classique reste NaN (déjà à largeur fixe).
        /// </summary>
        protected override Size MeasureOverride(Size availableSize)
        {
            ItemWidth = ThemeState.IsModern ? 195 : double.NaN;
            return base.MeasureOverride(availableSize);
        }

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
