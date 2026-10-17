using System;
using System.Collections.Generic;
using System.Linq;

namespace WaseBoard.Services
{
    /// <summary>
    /// Garde en mémoire (jamais sur disque) la durée des dernières lectures demandées au serveur : temps total vu par
    /// le client entre l'envoi de la requête et la réponse, et temps de traitement annoncé par le serveur (champ « ms »
    /// de la réponse de /play, absent sur un serveur plus ancien). Alimente la page Paramètres › Diagnostic.
    /// </summary>
    public class LatencyRecorder
    {
        public const int Capacity = 50;

        public readonly record struct Sample(DateTime At, long TotalMs, int? ServerMs);

        public readonly record struct Summary(int Count, long MedianTotalMs, long MaxTotalMs, int? MedianServerMs);

        private readonly object _lock = new();
        private readonly Queue<Sample> _samples = new();

        public void Add(long totalMs, int? serverMs)
        {
            lock (_lock)
            {
                _samples.Enqueue(new Sample(DateTime.Now, totalMs, serverMs));
                while (_samples.Count > Capacity) _samples.Dequeue();
            }
        }

        public IReadOnlyList<Sample> Snapshot()
        {
            lock (_lock) return _samples.ToList();
        }

        /// <summary>Résumé des <paramref name="last"/> dernières lectures ; null s'il n'y en a aucune.</summary>
        public Summary? Summarize(int last = 20)
        {
            var window = Snapshot().TakeLast(Math.Max(1, last)).ToList();
            if (window.Count == 0) return null;

            var totals = window.Select(s => s.TotalMs).OrderBy(v => v).ToList();
            var servers = window.Where(s => s.ServerMs.HasValue).Select(s => (long)s.ServerMs!.Value).OrderBy(v => v).ToList();
            return new Summary(
                window.Count,
                Median(totals),
                totals[^1],
                servers.Count == 0 ? null : (int)Median(servers));
        }

        /// <summary>Médiane d'une liste triée (moyenne des deux valeurs centrales si le nombre est pair).</summary>
        public static long Median(IReadOnlyList<long> sorted) =>
            sorted.Count == 0 ? 0
            : sorted.Count % 2 == 1 ? sorted[sorted.Count / 2]
            : (sorted[sorted.Count / 2 - 1] + sorted[sorted.Count / 2]) / 2;
    }
}
