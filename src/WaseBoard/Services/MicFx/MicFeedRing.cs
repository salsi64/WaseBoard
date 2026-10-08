using System;
using System.IO;
using System.IO.MemoryMappedFiles;
using System.Threading;

namespace WaseBoard.Services.MicFx
{
    /// <summary>Côté écrivain de la mémoire partagée lue par l'effet micro (APO WaseBoardMicFx, dans
    /// audiodg.exe). Miroir de native/WaseBoardMicFx/src/MicFeedProtocol.h : toute modification du
    /// protocole se fait des deux côtés, avec montée de Version.
    ///
    /// C'est l'APO qui crée la mémoire (un processus utilisateur n'a pas le droit de créer un objet
    /// Global\) : tant qu'aucune application n'écoute un micro équipé, TryOpen renvoie null.</summary>
    internal sealed class MicFeedRing : IDisposable
    {
        public const string MappingName = @"Global\WaseBoardMicFeed";
        public const uint Magic = 0x464D4257; // "WBMF"
        public const uint Version = 1;
        public const int SampleRate = 48000;
        public const int Capacity = 65536;     // frames, puissance de 2
        public const int LeadFrames = 2880;    // 60 ms d'avance visée sur l'horloge murale

        private const int HeaderSize = 64;
        private const long TotalSize = HeaderSize + Capacity * sizeof(float);

        private const int OffMagic = 0;
        private const int OffVersion = 4;
        private const int OffSampleRate = 8;
        private const int OffCapacity = 12;
        private const int OffWritePos = 16;
        private const int OffReadTick = 24;
        private const int OffReaderRate = 32;
        private const int OffReaderChannels = 36;
        private const int OffWriterPid = 40;
        private const int OffWriterTick = 48;

        private readonly MemoryMappedFile _file;
        private readonly MemoryMappedViewAccessor _view;

        private MicFeedRing(MemoryMappedFile file, MemoryMappedViewAccessor view)
        {
            _file = file;
            _view = view;
        }

        /// <summary>Ouvre la mémoire partagée si l'effet l'a créée et qu'elle est compatible, sinon null.</summary>
        public static MicFeedRing? TryOpen()
        {
            MemoryMappedFile? file = null;
            try
            {
                file = MemoryMappedFile.OpenExisting(MappingName, MemoryMappedFileRights.ReadWrite);
                var view = file.CreateViewAccessor(0, TotalSize, MemoryMappedFileAccess.ReadWrite);
                var ring = new MicFeedRing(file, view);
                if (ring.IsCompatible) return ring;
                ring.Dispose();
                return null;
            }
            catch (Exception ex) when (ex is FileNotFoundException or IOException or UnauthorizedAccessException or ArgumentException)
            {
                file?.Dispose();
                return null;
            }
        }

        /// <summary>En-tête initialisé par l'APO et de la même version de protocole que ce client.</summary>
        public bool IsCompatible =>
            _view.ReadUInt32(OffMagic) == Magic
            && _view.ReadUInt32(OffVersion) == Version
            && _view.ReadUInt32(OffSampleRate) == SampleRate
            && _view.ReadUInt32(OffCapacity) == Capacity;

        public long WritePos => _view.ReadInt64(OffWritePos);

        /// <summary>Instant (Environment.TickCount64) du dernier passage d'un effet dans son traitement
        /// temps réel : récent = une application écoute en ce moment un micro équipé.</summary>
        public long ReadTick => _view.ReadInt64(OffReadTick);
        public uint ReaderRate => _view.ReadUInt32(OffReaderRate);
        public uint ReaderChannels => _view.ReadUInt32(OffReaderChannels);

        public uint WriterPid => _view.ReadUInt32(OffWriterPid);
        public long WriterTick => _view.ReadInt64(OffWriterTick);

        /// <summary>Ajoute des frames (48 kHz mono) à la suite de la ligne de temps, puis publie la
        /// nouvelle position : les échantillons sont visibles AVANT que writePos ne les annonce.</summary>
        public void Write(float[] buffer, int count)
        {
            var pos = WritePos;
            var first = (int)(pos & (Capacity - 1));
            var n1 = Math.Min(count, Capacity - first);
            _view.WriteArray(HeaderSize + (long)first * sizeof(float), buffer, 0, n1);
            if (n1 < count)
                _view.WriteArray(HeaderSize, buffer, n1, count - n1);

            Interlocked.MemoryBarrier();
            _view.Write(OffWritePos, pos + count);
        }

        /// <summary>Signale l'écrivain actif (diagnostic, et pour ne pas mélanger deux WaseBoard
        /// ouverts en même temps, ex : build normal + build Dev).</summary>
        public void Heartbeat()
        {
            _view.Write(OffWriterPid, (uint)Environment.ProcessId);
            _view.Write(OffWriterTick, Environment.TickCount64);
        }

        /// <summary>Un autre processus WaseBoard écrit déjà dans le micro (battement récent).</summary>
        public bool IsOwnedByAnotherWriter()
        {
            var pid = WriterPid;
            return pid != 0 && pid != (uint)Environment.ProcessId
                && Environment.TickCount64 - WriterTick < 2000;
        }

        public void Dispose()
        {
            _view.Dispose();
            _file.Dispose();
        }
    }
}
