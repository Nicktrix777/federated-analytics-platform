package com.federatedanalytics.trino.tally;

import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.util.Arrays;
import java.util.Base64;
import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;

/**
 * Decrypt-only Java port of core-api/crypto/crypto.go's exact scheme
 * (AES-256-GCM, base64-encoded nonce+ciphertext) - this plugin only ever
 * reads secrets core-api already wrote, never writes any itself.
 */
public class AesGcmCrypto {
    private static final int NONCE_LENGTH_BYTES = 12;
    private static final int TAG_LENGTH_BITS = 128;

    private final SecretKeySpec key;

    public AesGcmCrypto(String base64Key) {
        byte[] keyBytes = Base64.getDecoder().decode(base64Key);
        if (keyBytes.length != 32) {
            throw new IllegalArgumentException(
                    "invalid encryption key: expected 32 bytes after base64 decoding, got " + keyBytes.length);
        }
        this.key = new SecretKeySpec(keyBytes, "AES");
    }

    public String decrypt(String encoded) {
        if (encoded == null || encoded.isEmpty()) {
            return "";
        }
        byte[] data = Base64.getDecoder().decode(encoded);
        if (data.length < NONCE_LENGTH_BYTES) {
            throw new IllegalArgumentException("invalid ciphertext: too short");
        }
        byte[] nonce = Arrays.copyOfRange(data, 0, NONCE_LENGTH_BYTES);
        byte[] ciphertext = Arrays.copyOfRange(data, NONCE_LENGTH_BYTES, data.length);
        try {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, key, new GCMParameterSpec(TAG_LENGTH_BITS, nonce));
            return new String(cipher.doFinal(ciphertext), StandardCharsets.UTF_8);
        } catch (GeneralSecurityException e) {
            throw new RuntimeException("failed to decrypt datasource credentials", e);
        }
    }
}
