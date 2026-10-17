using System.Buffers.Binary;
using WaseBoard.Services;

namespace WaseBoard.Tests;

public class PcmToolsTests
{
    private static byte[] Floats(params float[] values)
    {
        var bytes = new byte[values.Length * 4];
        for (var i = 0; i < values.Length; i++) BinaryPrimitives.WriteSingleLittleEndian(bytes.AsSpan(i * 4), values[i]);
        return bytes;
    }

    private static short[] Convert(byte[] source, PcmSampleKind kind, int channels)
    {
        var destination = new byte[PcmTools.OutputSize(source.Length, kind, channels)];
        var written = PcmTools.ConvertToPcm16(source, kind, channels, destination);
        Assert.Equal(destination.Length, written);
        var result = new short[written / 2];
        for (var i = 0; i < result.Length; i++) result[i] = BinaryPrimitives.ReadInt16LittleEndian(destination.AsSpan(i * 2));
        return result;
    }

    [Fact]
    public void Float_samples_are_scaled_to_16_bits()
    {
        var result = Convert(Floats(0f, 0.5f, -0.5f, 1f), PcmSampleKind.Float32, 1);

        Assert.Equal(new short[] { 0, 16384, -16384, 32767 }, result);
    }

    [Fact]
    public void Out_of_range_and_invalid_floats_are_clamped_not_wrapped()
    {
        var result = Convert(Floats(1.5f, -2f, float.NaN, float.PositiveInfinity), PcmSampleKind.Float32, 1);

        Assert.Equal(new short[] { 32767, -32767, 0, 32767 }, result);
    }

    [Fact]
    public void Sixteen_bit_samples_pass_through_almost_unchanged()
    {
        var source = new byte[6];
        BinaryPrimitives.WriteInt16LittleEndian(source.AsSpan(0), 1000);
        BinaryPrimitives.WriteInt16LittleEndian(source.AsSpan(2), -1000);
        BinaryPrimitives.WriteInt16LittleEndian(source.AsSpan(4), short.MinValue);

        var result = Convert(source, PcmSampleKind.Int16, 1);

        Assert.InRange(result[0], 999, 1000);
        Assert.InRange(result[1], -1000, -999);
        Assert.Equal(-32767, result[2]);
    }

    [Fact]
    public void Twenty_four_bit_samples_are_sign_extended()
    {
        // 0x7FFFFF = pleine échelle positive, 0x800000 = pleine échelle négative, 0xFFFFFF = −1 (≈ 0).
        var source = new byte[] { 0xFF, 0xFF, 0x7F, 0x00, 0x00, 0x80, 0xFF, 0xFF, 0xFF };

        var result = Convert(source, PcmSampleKind.Int24, 1);

        Assert.Equal(32767, result[0]);
        Assert.Equal(-32767, result[1]);
        Assert.Equal(0, result[2]);
    }

    [Fact]
    public void Thirty_two_bit_integer_samples_are_scaled()
    {
        var source = new byte[8];
        BinaryPrimitives.WriteInt32LittleEndian(source.AsSpan(0), int.MaxValue / 2);
        BinaryPrimitives.WriteInt32LittleEndian(source.AsSpan(4), int.MinValue);

        var result = Convert(source, PcmSampleKind.Int32, 1);

        Assert.InRange(result[0], 16383, 16384);
        Assert.Equal(-32767, result[1]);
    }

    [Fact]
    public void Stereo_keeps_left_and_right_in_order()
    {
        var result = Convert(Floats(0.5f, -0.25f, 0.1f, 0.2f), PcmSampleKind.Float32, 2);

        Assert.Equal(4, result.Length);
        Assert.Equal(16384, result[0]);
        Assert.InRange(result[1], -8193, -8192);
        Assert.InRange(result[2], 3276, 3277);
        Assert.InRange(result[3], 6553, 6554);
    }

    [Fact]
    public void Surround_5_1_keeps_the_center_in_both_ears_and_the_rears_on_their_side()
    {
        // Ordre Windows : avant G, avant D, centre, basses, arrière G, arrière D.
        var centerOnly = Convert(Floats(0, 0, 1f, 0, 0, 0), PcmSampleKind.Float32, 6);
        var rearLeftOnly = Convert(Floats(0, 0, 0, 0, 1f, 0), PcmSampleKind.Float32, 6);
        var lfeOnly = Convert(Floats(0, 0, 0, 1f, 0, 0), PcmSampleKind.Float32, 6);

        Assert.Equal(2, centerOnly.Length);
        Assert.InRange(centerOnly[0], 23000, 23300);   // ≈ 0,707 × pleine échelle
        Assert.Equal(centerOnly[0], centerOnly[1]);
        Assert.InRange(rearLeftOnly[0], 16383, 16384); // 0,5 à gauche seulement
        Assert.Equal(0, rearLeftOnly[1]);
        Assert.Equal(new short[] { 0, 0 }, lfeOnly);   // les basses sont laissées de côté
    }

    [Fact]
    public void Surround_7_1_adds_the_side_channels()
    {
        // Avant G/D, centre, basses, arrière G/D, côté G/D.
        var sideRightOnly = Convert(Floats(0, 0, 0, 0, 0, 0, 0, 1f), PcmSampleKind.Float32, 8);

        Assert.Equal(0, sideRightOnly[0]);
        Assert.InRange(sideRightOnly[1], 16383, 16384);
    }

    [Fact]
    public void A_loud_surround_mix_is_clipped_not_wrapped()
    {
        var result = Convert(Floats(1f, 1f, 1f, 0, 1f, 1f), PcmSampleKind.Float32, 6);

        Assert.Equal(new short[] { 32767, 32767 }, result);
    }

    [Fact]
    public void Mono_stays_mono()
    {
        Assert.Equal(1, PcmTools.OutputChannels(1));
        Assert.Equal(2, PcmTools.OutputChannels(2));
        Assert.Equal(2, PcmTools.OutputChannels(8));
        Assert.Equal(2, Convert(Floats(0.5f, 0.25f), PcmSampleKind.Float32, 1).Length); // 2 trames mono = 2 échantillons
    }

    [Fact]
    public void A_partial_frame_at_the_end_is_dropped()
    {
        var source = new byte[4 * 2 + 3]; // 2 trames mono flottantes + 3 octets en trop

        Assert.Equal(2 * 2, PcmTools.OutputSize(source.Length, PcmSampleKind.Float32, 1));
        Assert.Equal(2, Convert(source, PcmSampleKind.Float32, 1).Length);
    }

    [Fact]
    public void A_destination_that_is_too_small_is_refused()
    {
        Assert.Throws<ArgumentException>(() => PcmTools.ConvertToPcm16(Floats(0, 0, 0, 0), PcmSampleKind.Float32, 2, new byte[2]));
    }

    [Fact]
    public void Peak_level_finds_the_loudest_sample_and_recognizes_silence()
    {
        var pcm = new byte[8];
        BinaryPrimitives.WriteInt16LittleEndian(pcm.AsSpan(2), -16384);
        BinaryPrimitives.WriteInt16LittleEndian(pcm.AsSpan(6), 8192);

        Assert.Equal(0.5, PcmTools.PeakLevel(pcm), 3);
        Assert.Equal(0.0, PcmTools.PeakLevel(new byte[1000]));
        Assert.Equal(0.0, PcmTools.PeakLevel(ReadOnlySpan<byte>.Empty));
    }

    [Fact]
    public void Wav_header_describes_the_data()
    {
        var header = new byte[44];
        PcmTools.WriteWavHeader(header, dataBytes: 96000, sampleRate: 48000, channels: 2);

        Assert.Equal("RIFF", System.Text.Encoding.ASCII.GetString(header, 0, 4));
        Assert.Equal(36 + 96000, BitConverter.ToInt32(header, 4));
        Assert.Equal("WAVEfmt ", System.Text.Encoding.ASCII.GetString(header, 8, 8));
        Assert.Equal(1, BitConverter.ToInt16(header, 20));               // PCM
        Assert.Equal(2, BitConverter.ToInt16(header, 22));
        Assert.Equal(48000, BitConverter.ToInt32(header, 24));
        Assert.Equal(48000 * 2 * 2, BitConverter.ToInt32(header, 28));   // octets par seconde
        Assert.Equal(4, BitConverter.ToInt16(header, 32));               // octets par trame
        Assert.Equal(16, BitConverter.ToInt16(header, 34));
        Assert.Equal("data", System.Text.Encoding.ASCII.GetString(header, 36, 4));
        Assert.Equal(96000, BitConverter.ToInt32(header, 40));
    }

    [Fact]
    public void Wav_file_has_header_plus_data_and_a_correct_duration()
    {
        var path = Path.Combine(Path.GetTempPath(), "waseboard-test-" + Guid.NewGuid().ToString("N") + ".wav");
        try
        {
            var pcm = new byte[48000 * 2 * 2]; // 1 s stéréo
            pcm[100] = 42;
            PcmTools.WriteWavFile(path, pcm, 48000, 2);

            var bytes = File.ReadAllBytes(path);
            Assert.Equal(44 + pcm.Length, bytes.Length);
            Assert.Equal(pcm.Length, BitConverter.ToInt32(bytes, 40));
            Assert.Equal(42, bytes[44 + 100]);
        }
        finally
        {
            File.Delete(path);
        }
    }

    [Fact]
    public void Header_buffer_must_hold_44_bytes()
    {
        Assert.Throws<ArgumentException>(() => PcmTools.WriteWavHeader(new byte[10], 0, 48000, 2));
    }
}
