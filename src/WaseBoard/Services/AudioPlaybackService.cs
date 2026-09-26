using System;
using System.Collections.Generic;
using System.Linq;
using NAudio.CoreAudioApi;
using NAudio.Wave;

namespace WaseBoard.Services
{
    /// <summary>Enveloppe un WaveStream pour ne jamais délivrer plus d'octets que sa longueur
    /// nominale (certains décodeurs VBR/WASAPI peuvent sinon boucler en fin de lecture).</summary>
    internal sealed class SingleShotWaveStream : WaveStream
    {
        private readonly WaveStream _source;
        private long _bytesDelivered;

        public SingleShotWaveStream(WaveStream source) => _source = source;

        public override WaveFormat WaveFormat => _source.WaveFormat;
        public override long Length => _source.Length;

        public override long Position
        {
            get => _source.Position;
            set { _source.Position = value; _bytesDelivered = value; }
        }

        public override int Read(byte[] buffer, int offset, int count)
        {
            if (_bytesDelivered >= _source.Length) return 0;

            int allowed = (int)Math.Min(count, _source.Length - _bytesDelivered);
            if (allowed <= 0) return 0;

            int read = _source.Read(buffer, offset, allowed);
            _bytesDelivered += read;
            return read;
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing) _source.Dispose();
            base.Dispose(disposing);
        }
    }

    /// <summary>Joue des fichiers audio vers un ou plusieurs périphériques de sortie (retour audio local).</summary>
    public class AudioPlaybackService : IDisposable
    {
        private class ActivePlayback
        {
            public string SoundId = "";
            public WasapiOut Output = null!;
            public WaveStream Reader = null!;
        }

        private readonly List<ActivePlayback> _active = new();
        private readonly object _lock = new();

        /// <summary>Déclenché quand un son démarre sa lecture (une seule fois, même s'il joue sur plusieurs périphériques).</summary>
        public event Action<string>? SoundStarted;

        /// <summary>Déclenché quand un son a fini de jouer sur TOUS ses périphériques (plus aucune instance active).</summary>
        public event Action<string>? SoundStopped;

        /// <summary>Périphérique de sortie par défaut du système, utilisé pour le retour audio local.</summary>
        public static MMDevice? GetDefaultOutputDevice()
        {
            try
            {
                using var enumerator = new MMDeviceEnumerator();
                return enumerator.GetDefaultAudioEndpoint(DataFlow.Render, Role.Multimedia);
            }
            catch (Exception)
            {
                return null;
            }
        }

        /// <summary>Un périphérique de sortie ciblé, avec son propre volume (ex: micro virtuel à 100%, écoute à 60%).</summary>
        public readonly record struct PlaybackTarget(MMDevice Device, float Volume);

        /// <summary>Joue un son vers plusieurs périphériques (chacun son volume). Un son déjà en
        /// cours est stoppé puis relancé, plutôt que superposé ; les autres sons ne sont pas affectés.</summary>
        public void PlaySound(string soundId, string filePath, IEnumerable<PlaybackTarget> targets)
        {
            StopSound(soundId);
            SoundStarted?.Invoke(soundId);

            foreach (var target in targets)
            {
                if (target.Device is null) continue;

                WaveStream? reader = null;
                WasapiOut? output = null;
                try
                {
                    // La fabrique choisit automatiquement le bon décodeur (AudioFileReader pour
                    // wav/mp3/etc., NAudio.Vorbis pour l'ogg) et expose un moyen uniforme de régler le volume.
                    reader = AudioReaderFactory.OpenForPlayback(filePath, out var setVolume);
                    setVolume(target.Volume);

                    output = new WasapiOut(target.Device, AudioClientShareMode.Shared, true, 100);
                    output.Init(new SingleShotWaveStream(reader)); // garantit une lecture strictement unique

                    var entry = new ActivePlayback { SoundId = soundId, Output = output, Reader = reader };

                    output.PlaybackStopped += (s, e) =>
                    {
                        bool anyRemainingForSound;
                        lock (_lock)
                        {
                            _active.Remove(entry);
                            anyRemainingForSound = _active.Any(a => a.SoundId == soundId);
                        }
                        reader.Dispose();
                        output.Dispose();

                        if (!anyRemainingForSound) SoundStopped?.Invoke(soundId);
                    };

                    lock (_lock) _active.Add(entry);
                    output.Play();
                }
                catch (Exception)
                {
                    reader?.Dispose();
                    output?.Dispose();
                    // Un périphérique indisponible ne doit pas empêcher la lecture sur les autres.
                }
            }
        }

        /// <summary>Arrête toutes les instances de lecture actives pour un son donné (tous périphériques confondus).</summary>
        public void StopSound(string soundId)
        {
            List<ActivePlayback> toStop;
            lock (_lock) toStop = _active.Where(a => a.SoundId == soundId).ToList();

            foreach (var entry in toStop)
            {
                try { entry.Output.Stop(); } catch { /* déjà arrêté */ }
            }
        }

        public void StopAll()
        {
            List<ActivePlayback> snapshot;
            lock (_lock) snapshot = _active.ToList();

            foreach (var entry in snapshot)
            {
                try { entry.Output.Stop(); } catch { /* déjà arrêté */ }
            }
        }

        public void Dispose() => StopAll();
    }
}
