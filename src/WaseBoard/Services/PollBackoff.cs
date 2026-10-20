using System;
using System.Collections.Generic;
using System.Linq;

namespace WaseBoard.Services
{
    /// <summary>
    /// Rythme d'un sondage répété (ici : « qui joue quoi », toutes les 300 ms). Tant que les réponses arrivent, il reste au rythme normal et
    /// ne fait RIEN d'autre que remettre un compteur à zéro. Après <c>failuresBeforeSlowing</c> échecs de suite (serveur injoignable, clé du
    /// serveur refusée…), il ralentit par paliers — à chaque échec de plus, le palier suivant, jusqu'au dernier — pour ne pas marteler un
    /// serveur qui répond non, des heures durant. Au premier succès, retour immédiat au rythme normal.
    ///
    /// Sans WPF ni réseau : le minuteur, lui, est géré par l'appelant (voir MainWindow.PollActivityAsync).
    /// </summary>
    public sealed class PollBackoff
    {
        private readonly TimeSpan[] _slowSteps;
        private readonly int _failuresBeforeSlowing;

        public PollBackoff(TimeSpan normal, IReadOnlyList<TimeSpan> slowSteps, int failuresBeforeSlowing = 3)
        {
            if (slowSteps.Count == 0) throw new ArgumentException("au moins un palier de ralentissement", nameof(slowSteps));
            if (failuresBeforeSlowing < 1) throw new ArgumentOutOfRangeException(nameof(failuresBeforeSlowing));
            Normal = normal;
            Interval = normal;
            _slowSteps = slowSteps.ToArray();
            _failuresBeforeSlowing = failuresBeforeSlowing;
        }

        /// <summary>Intervalle de référence (celui d'un fonctionnement normal).</summary>
        public TimeSpan Normal { get; }

        /// <summary>Intervalle à appliquer au minuteur en ce moment.</summary>
        public TimeSpan Interval { get; private set; }

        /// <summary>Échecs consécutifs depuis le dernier succès.</summary>
        public int ConsecutiveFailures { get; private set; }

        /// <summary>Le sondage tourne-t-il au ralenti ?</summary>
        public bool IsSlowed => Interval != Normal;

        /// <summary>Un sondage a échoué. Renvoie vrai si l'intervalle vient de changer (l'appelant doit alors le réappliquer au minuteur).</summary>
        public bool RecordFailure()
        {
            if (ConsecutiveFailures < int.MaxValue) ConsecutiveFailures++;
            if (ConsecutiveFailures < _failuresBeforeSlowing) return false;

            var step = Math.Min(ConsecutiveFailures - _failuresBeforeSlowing, _slowSteps.Length - 1);
            return Apply(_slowSteps[step]);
        }

        /// <summary>Un sondage a réussi. Chemin le plus fréquent : sans aucun échec en cours, il ne fait que le constater (aucune écriture).
        /// Renvoie vrai si l'intervalle vient de revenir au rythme normal.</summary>
        public bool RecordSuccess()
        {
            if (ConsecutiveFailures == 0) return false;
            ConsecutiveFailures = 0;
            return Apply(Normal);
        }

        /// <summary>Repart de zéro (réglages changés, actualisation demandée…). Renvoie vrai si l'intervalle a changé.</summary>
        public bool Reset()
        {
            ConsecutiveFailures = 0;
            return Apply(Normal);
        }

        private bool Apply(TimeSpan interval)
        {
            if (Interval == interval) return false;
            Interval = interval;
            return true;
        }
    }
}
