// Packages microphone samples into small LINEAR16 frames for streaming STT.
class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.samples = new Float32Array(2048);
    this.offset = 0;
  }

  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;

    let sourceOffset = 0;
    while (sourceOffset < channel.length) {
      const count = Math.min(channel.length - sourceOffset, this.samples.length - this.offset);
      this.samples.set(channel.subarray(sourceOffset, sourceOffset + count), this.offset);
      this.offset += count;
      sourceOffset += count;

      if (this.offset === this.samples.length) {
        const pcm = new Int16Array(this.samples.length);
        let squareSum = 0;
        for (let i = 0; i < this.samples.length; i += 1) {
          const sample = Math.max(-1, Math.min(1, this.samples[i]));
          pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
          squareSum += sample * sample;
        }
        this.port.postMessage(
          { pcm: pcm.buffer, rms: Math.sqrt(squareSum / this.samples.length) },
          [pcm.buffer],
        );
        this.offset = 0;
      }
    }
    return true;
  }
}

registerProcessor("pcm-capture", PcmCaptureProcessor);
