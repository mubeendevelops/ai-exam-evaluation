/**
 * Reader for the server's common-password Bloom filter (format ``TBF1``, see
 * ``tarn_adapters/auth/passwords.py``). It is an early hint only: the server decides.
 */
export interface Bloom {
  mightContain(word: string): Promise<boolean>
}

const MAGIC = [0x54, 0x42, 0x46, 0x31] // "TBF1"

export function parseBloom(buffer: ArrayBuffer): Bloom {
  const bytes = new Uint8Array(buffer)
  if (bytes.length < 9 || MAGIC.some((b, i) => bytes[i] !== b)) {
    throw new Error('not a TBF1 Bloom filter')
  }
  const view = new DataView(buffer)
  const m = view.getUint32(4, false)
  const k = bytes[8] as number
  const bits = bytes.subarray(9)
  if (bits.length < Math.ceil(m / 8)) throw new Error('truncated Bloom filter')

  return {
    async mightContain(word: string): Promise<boolean> {
      const subtle = globalThis.crypto?.subtle
      if (!subtle) return false // insecure context: no hint, the server still checks
      const digest = new DataView(
        await subtle.digest('SHA-256', new TextEncoder().encode(word.toLowerCase())),
      )
      const h1 = digest.getUint32(0, false)
      const h2 = (digest.getUint32(4, false) | 1) >>> 0
      for (let i = 0; i < k; i++) {
        // (h1 + i * h2) mod m with BigInt: the product exceeds 2^53.
        const position = Number((BigInt(h1) + BigInt(i) * BigInt(h2)) % BigInt(m))
        if (((bits[position >> 3] as number) & (1 << (position & 7))) === 0) return false
      }
      return true
    },
  }
}
