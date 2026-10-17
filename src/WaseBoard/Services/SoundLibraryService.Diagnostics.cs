using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Net.Http;
using System.Text.Json;
using System.Threading.Tasks;

namespace WaseBoard.Services
{
    // Mesures pour la page Paramètres › Diagnostic : latence réseau vers le serveur, durée des lectures, version annoncée.
    public partial class SoundLibraryService
    {
        /// <summary>Durée des dernières lectures (clic → réponse du serveur), en mémoire seulement.</summary>
        public LatencyRecorder Latency { get; } = new();

        /// <summary>Version annoncée par le serveur (/status) ; null tant qu'elle n'est pas connue ou si le serveur est ancien.</summary>
        public string? ServerVersion { get; private set; }

        private void ReadServerVersion(JsonElement root)
        {
            ServerVersion = root.TryGetProperty("server_version", out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;
        }

        /// <summary>Résultat d'une série de mesures : durées (ms) des réponses réussies, ou la raison de l'échec.</summary>
        public readonly record struct LatencyProbeResult(IReadOnlyList<long> SamplesMs, string? Error)
        {
            public bool Ok => Error is null && SamplesMs.Count > 0;
            public long Median => LatencyRecorder.Median(SamplesMs.OrderBy(v => v).ToList());
            public long Min => SamplesMs.Count == 0 ? 0 : SamplesMs.Min();
            public long Max => SamplesMs.Count == 0 ? 0 : SamplesMs.Max();
        }

        /// <summary>
        /// Mesure le temps de réponse de GET /health (route publique, sans authentification : on mesure le réseau et le
        /// serveur, pas la vérification d'identité). La première requête, qui peut inclure l'établissement de la
        /// connexion sécurisée, est écartée comme échauffement.
        /// </summary>
        public async Task<LatencyProbeResult> MeasureLatencyAsync(int samples = 5)
        {
            var results = new List<long>();
            try
            {
                for (var i = 0; i < samples + 1; i++)
                {
                    using var request = new HttpRequestMessage(HttpMethod.Get, BaseUrl + "/health");
                    var timer = Stopwatch.StartNew();
                    using var response = await _http.SendAsync(request);
                    timer.Stop();
                    if (i > 0) results.Add(timer.ElapsedMilliseconds);
                }
                return new LatencyProbeResult(results, null);
            }
            catch (Exception ex)
            {
                return new LatencyProbeResult(results, ex.Message);
            }
        }
    }
}
