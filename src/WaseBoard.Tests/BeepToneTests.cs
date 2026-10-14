using WaseBoard.Services;

namespace WaseBoard.Tests;

public class BeepToneTests
{
    private static short Sample(byte[] wav, int index) => BitConverter.ToInt16(wav, 44 + index * 2);

    [Fact]
    public void Wav_header_is_valid_and_sizes_match()
    {
        var wav = BeepTone.BuildWav(BeepTone.RisingTones, noteMs: 100);

        Assert.Equal("RIFF", System.Text.Encoding.ASCII.GetString(wav, 0, 4));
        Assert.Equal("WAVE", System.Text.Encoding.ASCII.GetString(wav, 8, 4));
        Assert.Equal("fmt ", System.Text.Encoding.ASCII.GetString(wav, 12, 4));
        Assert.Equal("data", System.Text.Encoding.ASCII.GetString(wav, 36, 4));
        Assert.Equal(1, BitConverter.ToInt16(wav, 20));                       // PCM
        Assert.Equal(1, BitConverter.ToInt16(wav, 22));                       // mono
        Assert.Equal(BeepTone.SampleRate, BitConverter.ToInt32(wav, 24));
        Assert.Equal(16, BitConverter.ToInt16(wav, 34));

        var dataBytes = BitConverter.ToInt32(wav, 40);
        Assert.Equal(wav.Length - 44, dataBytes);
        Assert.Equal(wav.Length - 8, BitConverter.ToInt32(wav, 4));
        var seconds = dataBytes / 2.0 / BeepTone.SampleRate;
        Assert.InRange(seconds, 0.199, 0.201);                                // 2 notes de 100 ms
    }

    [Fact]
    public void Each_note_starts_and_ends_silently_to_avoid_clicks()
    {
        var wav = BeepTone.BuildWav(new[] { 880 }, noteMs: 90);
        var samples = (wav.Length - 44) / 2;

        Assert.Equal(0, Sample(wav, 0));
        Assert.True(Math.Abs((int)Sample(wav, samples - 1)) < 200);           // fondu de sortie
    }

    [Fact]
    public void The_tone_has_the_requested_pitch_and_a_safe_volume()
    {
        var wav = BeepTone.BuildWav(new[] { 1000 }, noteMs: 200, volume: 0.35);
        var samples = (wav.Length - 44) / 2;

        var crossings = 0;
        var peak = 0;
        for (var i = 1; i < samples; i++)
        {
            if (Sample(wav, i - 1) <= 0 && Sample(wav, i) > 0) crossings++;
            peak = Math.Max(peak, Math.Abs((int)Sample(wav, i)));
        }

        var frequency = crossings / (samples / (double)BeepTone.SampleRate);
        Assert.InRange(frequency, 950, 1050);
        Assert.InRange(peak, 0.30 * short.MaxValue, 0.36 * short.MaxValue);   // jamais à pleine échelle
    }

    [Fact]
    public void Rising_and_falling_signals_differ_but_have_the_same_length()
    {
        var up = BeepTone.BuildWav(BeepTone.RisingTones);
        var down = BeepTone.BuildWav(BeepTone.FallingTones);

        Assert.Equal(up.Length, down.Length);
        Assert.False(up.AsSpan(44).SequenceEqual(down.AsSpan(44)));
        Assert.True(BeepTone.RisingTones[1] > BeepTone.RisingTones[0]);
        Assert.True(BeepTone.FallingTones[1] < BeepTone.FallingTones[0]);
    }

    [Fact]
    public void Invalid_arguments_are_rejected()
    {
        Assert.Throws<ArgumentException>(() => BeepTone.BuildWav(Array.Empty<int>()));
        Assert.Throws<ArgumentOutOfRangeException>(() => BeepTone.BuildWav(new[] { 440 }, noteMs: 5));
    }

    [Fact]
    public void The_clip_signal_is_a_single_note_distinct_from_the_mode_signals()
    {
        var clip = BeepTone.BuildWav(BeepTone.ClipTones, noteMs: 110);

        Assert.Single(BeepTone.ClipTones);
        Assert.DoesNotContain(BeepTone.ClipTones[0], BeepTone.RisingTones);
        Assert.DoesNotContain(BeepTone.ClipTones[0], BeepTone.FallingTones);
        Assert.Equal(44 + BeepTone.SampleRate * 110 / 1000 * 2, clip.Length);
    }
}
