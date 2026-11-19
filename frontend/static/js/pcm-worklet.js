// AudioWorklet: downsample the microphone to 16 kHz mono and post 20 ms-multiple PCM16 blocks.
class PcmProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.pos = 0;
    this.prev = 0;
    this.out = new Int16Array(1600); // 100 ms
    this.n = 0;
    this.sumSq = 0;
    this.cnt = 0;
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      // linear interpolation resampler with a cheap box filter (average of the samples in each step)
      this.pos += 1;
      this.acc = (this.acc || 0) + ch[i];
      this.accN = (this.accN || 0) + 1;
      if (this.pos >= this.ratio) {
        this.pos -= this.ratio;
        const v = Math.max(-1, Math.min(1, this.acc / this.accN));
        this.acc = 0; this.accN = 0;
        this.out[this.n++] = v < 0 ? v * 32768 : v * 32767;
        this.sumSq += v * v; this.cnt++;
        if (this.n === this.out.length) {
          const level = Math.sqrt(this.sumSq / Math.max(1, this.cnt));
          this.port.postMessage({ pcm: this.out.buffer, level }, [this.out.buffer]);
          this.out = new Int16Array(1600);
          this.n = 0; this.sumSq = 0; this.cnt = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor("pcm-processor", PcmProcessor);
