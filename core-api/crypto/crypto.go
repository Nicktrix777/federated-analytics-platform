// Package crypto encrypts datasource credentials at rest with AES-256-GCM.
package crypto

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
)

// Encryptor encrypts and decrypts secrets using a single AES-256-GCM key.
type Encryptor struct {
	gcm cipher.AEAD
}

// NewEncryptor builds an Encryptor from a base64-encoded 32-byte AES-256 key
// (e.g. generated with `openssl rand -base64 32`).
func NewEncryptor(base64Key string) (*Encryptor, error) {
	key, err := base64.StdEncoding.DecodeString(base64Key)
	if err != nil {
		return nil, fmt.Errorf("invalid encryption key: not valid base64: %w", err)
	}
	if len(key) != 32 {
		return nil, fmt.Errorf("invalid encryption key: expected 32 bytes after base64 decoding, got %d", len(key))
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, fmt.Errorf("failed to init AES cipher: %w", err)
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, fmt.Errorf("failed to init GCM: %w", err)
	}
	return &Encryptor{gcm: gcm}, nil
}

// Encrypt returns a base64-encoded nonce+ciphertext. Empty input passes through as
// empty output so datasources without a password aren't wrapped in ciphertext.
func (e *Encryptor) Encrypt(plaintext string) (string, error) {
	if plaintext == "" {
		return "", nil
	}
	nonce := make([]byte, e.gcm.NonceSize())
	if _, err := io.ReadFull(rand.Reader, nonce); err != nil {
		return "", fmt.Errorf("failed to generate nonce: %w", err)
	}
	ciphertext := e.gcm.Seal(nonce, nonce, []byte(plaintext), nil)
	return base64.StdEncoding.EncodeToString(ciphertext), nil
}

// Decrypt reverses Encrypt.
func (e *Encryptor) Decrypt(encoded string) (string, error) {
	if encoded == "" {
		return "", nil
	}
	data, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil {
		return "", fmt.Errorf("invalid ciphertext: not valid base64: %w", err)
	}
	nonceSize := e.gcm.NonceSize()
	if len(data) < nonceSize {
		return "", errors.New("invalid ciphertext: too short")
	}
	nonce, ciphertext := data[:nonceSize], data[nonceSize:]
	plaintext, err := e.gcm.Open(nil, nonce, ciphertext, nil)
	if err != nil {
		return "", fmt.Errorf("failed to decrypt: %w", err)
	}
	return string(plaintext), nil
}
