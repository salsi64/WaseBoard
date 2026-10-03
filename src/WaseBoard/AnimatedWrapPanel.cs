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

        // Pas de largeur de cellule imposée : les pilules (thème moderne) prennent la largeur de leur nom
        // (96 → 210 px, voir ModernSoundButtonTemplate) et ne changent plus de taille pendant la lecture
        // (l'avatar de qui joue remplace la pastille d'icône au lieu de s'ajouter), donc rien ne décale
        // leurs voisines. Les cartes classiques ont déjà une largeur fixe.

        // Mémorise la largeur disponible et la taille souhaitée de chaque enfant au dernier passage :
        // si rien de tout ça n'a changé, aucun enfant ne peut avoir bougé (le wrap d'un WrapPanel ne
        // dépend que de ça), donc inutile de capturer/comparer les positions réelles (TransformToAncestor,
        // deux fois par enfant) juste pour ne rien trouver. Sans ce garde-fou, un simple changement de
        // visibilité dans UN bouton (le halo du glow, déclenché à chaque clic et par le sondage régulier)
        // invalide l'arrangement de TOUT le panneau, et ce coût grossit avec le nombre de sons du
        // catalogue — perceptible comme une latence au clic sur un gros catalogue.
        private double _lastAvailableWidth = double.NaN;
        private Dictionary<UIElement, Size> _lastDesiredSizes = new();

        protected override Size ArrangeOverride(Size finalSize)
        {
            if (!LayoutMayHaveChanged(finalSize.Width))
                return base.ArrangeOverride(finalSize);

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

        /// <summary>Met à jour le relevé (largeur + taille souhaitée de chaque enfant) et renvoie
        /// true si quelque chose a changé depuis le dernier passage — seul cas où un enfant peut
        /// avoir changé de position dans un WrapPanel. Reconstruit le relevé à chaque appel (plutôt
        /// que de le modifier en place) : un enfant retiré du catalogue disparaît ainsi naturellement,
        /// sans quoi son entrée resterait indéfiniment et faussait la comparaison de nombre d'enfants.</summary>
        private bool LayoutMayHaveChanged(double availableWidth)
        {
            var changed = Math.Abs(availableWidth - _lastAvailableWidth) > 0.5
                          || InternalChildren.Count != _lastDesiredSizes.Count;
            _lastAvailableWidth = availableWidth;

            var current = new Dictionary<UIElement, Size>(InternalChildren.Count);
            foreach (UIElement child in InternalChildren)
            {
                current[child] = child.DesiredSize;
                if (!changed && (!_lastDesiredSizes.TryGetValue(child, out var previous) || previous != child.DesiredSize))
                    changed = true;
            }
            _lastDesiredSizes = current;

            return changed;
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
