using System;
using System.Collections.Generic;
using System.Linq;
using NAudio.CoreAudioApi;
using NAudio.CoreAudioApi.Interfaces;

namespace WaseBoard.Services.MicFx
{
    /// <summary>Activité de l'effet micro, micro par micro : une application écoute-t-elle ce micro, et
    /// l'effet y tourne-t-il vraiment ? « Écouté mais effet inactif » = micro qui n'accepte pas l'effet
    /// (pilote incompatible) ou application qui prend le micro en mode exclusif (les effets sont alors
    /// contournés) : les sons n'y passent pas, il faut le dire plutôt que laisser croire que ça marche.</summary>
    public static class MicFxDiagnostics
    {
        /// <summary>Au-delà, l'effet n'est plus considéré comme actif sur ce micro (il passe toutes les ~10 ms).</summary>
        private const long ActiveWindowMs = 1500;

        public sealed record MicActivity(string EndpointGuid, string Name, bool InUse, bool EffectActive)
        {
            /// <summary>Une application écoute ce micro, mais l'effet n'y tourne pas.</summary>
            public bool EffectMissing => InUse && !EffectActive;
        }

        /// <summary>État des micros équipés. Lecture seule, sans rien démarrer ; à appeler hors du fil UI si
        /// possible (énumération des sessions audio), quelques millisecondes en pratique.</summary>
        public static List<MicActivity> Probe(IEnumerable<MicFxSetup.MicInfo> microphones)
        {
            var result = new List<MicActivity>();
            using var ring = MicFeedRing.TryOpen();
            using var enumerator = new MMDeviceEnumerator();
            var now = Environment.TickCount64;

            foreach (var mic in microphones.Where(m => m.Equipped))
            {
                var tick = ring is not null && Guid.TryParse(mic.EndpointGuid, out var guid) ? ring.ReadEndpointTick(guid) : 0;
                var effectActive = tick != 0 && now - tick < ActiveWindowMs;
                result.Add(new MicActivity(mic.EndpointGuid, mic.Name, IsCaptured(enumerator, mic.EndpointGuid), effectActive));
            }
            return result;
        }

        /// <summary>Une application a-t-elle un flux de capture actif sur ce micro ?</summary>
        private static bool IsCaptured(MMDeviceEnumerator enumerator, string endpointGuid)
        {
            try
            {
                using var device = enumerator.GetDevice("{0.0.1.00000000}." + endpointGuid);
                var sessions = device.AudioSessionManager.Sessions;
                for (var i = 0; i < sessions.Count; i++)
                {
                    if (sessions[i].State == AudioSessionState.AudioSessionStateActive) return true;
                }
            }
            catch (Exception)
            {
                // Micro débranché entre-temps, service audio en redémarrage : « pas écouté ».
            }
            return false;
        }
    }
}
