// Audio-Brücke des Teams-Teilnehmers (Spike): wird VOR dem Laden der Seite eingespeist.
// Hören: jede eingehende WebRTC-Audiospur -> gemischt -> 16 kHz PCM16 -> window.__aieHoeren(base64)
// Sprechen: getUserMedia liefert einen Strom, den window.__aieSprechen(base64 PCM16 24 kHz) speist.
(() => {
  const ctx = new AudioContext({ sampleRate: 48000 });
  const mix = ctx.createGain();
  // Hören — ohne AudioWorklet: Ein nachgeladenes Modul (blob:/data:) kann die
  // Inhaltsrichtlinie (CSP) der Seite sperren. ScriptProcessor braucht kein Modul.
  const abgriff = ctx.createScriptProcessor(4096, 1, 1);
  let puffer = [], rest = 0;
  const ausgeben = () => {
    while (puffer.length >= 3200) { // 200 ms
      const pcm = new Int16Array(puffer.splice(0, 3200).map(v => Math.max(-1, Math.min(1, v)) * 0x7fff));
      const b = new Uint8Array(pcm.buffer); let s = ""; for (const x of b) s += String.fromCharCode(x);
      if (window.__aieHoeren) window.__aieHoeren(btoa(s));
    }
  };
  // Rückfall (Browser ohne MediaStreamTrackProcessor): über WebAudio.
  abgriff.onaudioprocess = (e) => {
    const ein = e.inputBuffer.getChannelData(0);
    // 48 kHz -> 16 kHz: Mittelwert aus je drei Werten (einfacher Tiefpass)
    for (let i = rest; i + 2 < ein.length; i += 3) puffer.push((ein[i] + ein[i + 1] + ein[i + 2]) / 3);
    rest = 0;
    ausgeben();
  };
  // Ein Knoten verarbeitet nur, wenn der Graph bis zum Ausgang reicht — stumm anhängen.
  const stumm = ctx.createGain(); stumm.gain.value = 0;
  mix.connect(abgriff); abgriff.connect(stumm); stumm.connect(ctx.destination);
  const bereit = Promise.resolve();
  const Original = window.RTCPeerConnection;
  window.RTCPeerConnection = function (...a) {
    const pc = new Original(...a);
    // Teams legt Empfänger oft schon im Wartebereich an; ein „track“-Ereignis kommt dann
    // nicht mehr (live gesehen 13.11.2026: 1 Verbindung, 0 Spuren). Deshalb zusätzlich die
    // Empfänger regelmäßig abfragen.
    const abfragen = setInterval(() => {
      if (pc.connectionState === "closed") return clearInterval(abfragen);
      for (const r of pc.getReceivers()) if (r.track) anhaengen(r.track);
    }, 2000);
    pc.addEventListener("track", (ev) => anhaengen(ev.track));
    return pc;
  };
  window.RTCPeerConnection.prototype = Original.prototype;
  const gesehen = new WeakSet();
  function anhaengen(track) {
    if (!track || track.kind !== "audio" || gesehen.has(track)) return;
    gesehen.add(track);
    window.__aieSpuren = (window.__aieSpuren || 0) + 1;
    const ev = { track };
    {
      // Bevorzugt: Audiodaten direkt aus der Spur (Insertable Streams). Braucht weder ein
      // Ausgabegerät (Behälter ohne Soundkarte: WebAudio bekäme dort nur Stille) noch ein
      // nachgeladenes Modul (CSP).
      // Chrome dekodiert eingehenden Ton nur, wenn ihn etwas abspielt: stummes Element.
      const el = new Audio(); el.muted = true; el.srcObject = new MediaStream([ev.track]); el.play().catch(() => {});
      if (window.MediaStreamTrackProcessor) {
        const leser = new MediaStreamTrackProcessor({ track: ev.track }).readable.getReader();
        (async () => {
          for (;;) {
            const { value: f, done } = await leser.read();
            if (done) break;
            const n = f.numberOfFrames, daten = new Float32Array(n);
            f.copyTo(daten, { planeIndex: 0, format: "f32-planar" });
            const schritt = f.sampleRate / 16000;
            for (let p = 0; p + schritt <= n; p += schritt) {
              let summe = 0, k = 0;
              for (let j = Math.floor(p); j < Math.floor(p + schritt); j++) { summe += daten[j]; k++; }
              puffer.push(k ? summe / k : 0);
            }
            f.close();
            ausgeben();
          }
        })();
        return;
      }
      bereit.then(() => {
        ctx.createMediaStreamSource(new MediaStream([ev.track])).connect(mix);
      });
    }
  }
  // Sprechen
  const ziel = ctx.createMediaStreamDestination();
  let naechsterStart = 0;
  window.__aieSprechen = (b64) => {
    const roh = atob(b64); const n = roh.length / 2; const buf = ctx.createBuffer(1, n, 24000); const kanal = buf.getChannelData(0);
    for (let i = 0; i < n; i++) { const v = roh.charCodeAt(2 * i) | (roh.charCodeAt(2 * i + 1) << 8); kanal[i] = (v > 32767 ? v - 65536 : v) / 32768; }
    const q = ctx.createBufferSource(); q.buffer = buf; q.connect(ziel);
    naechsterStart = Math.max(naechsterStart, ctx.currentTime); q.start(naechsterStart); naechsterStart += buf.duration;
  };
  const md = navigator.mediaDevices;
  // Teams fragt Mikrofon und Kamera oft zusammen ab. Eine Ablehnung der Kamera hält es für
  // „kein Audio“ — deshalb eine schwarze Ersatzkamera statt einer Ablehnung.
  const leinwand = Object.assign(document.createElement("canvas"), { width: 320, height: 180 });
  const malen = () => { const g = leinwand.getContext("2d"); g.fillStyle = "#000"; g.fillRect(0, 0, 320, 180); };
  md.getUserMedia = async (c) => {
    const spuren = [];
    if (!c || c.audio) spuren.push(...ziel.stream.getAudioTracks());
    if (c && c.video) { malen(); spuren.push(...leinwand.captureStream(1).getVideoTracks()); }
    return new MediaStream(spuren);
  };
  window.__aieKontext = ctx;
})();
