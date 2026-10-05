package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/http/httputil"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"
)

const (
	testAuthCredential  = "tskey-" + "auth-testvalue"
	testOAuthCredential = "tskey-" + "client-secretvalue"
)

func TestParseOptions(t *testing.T) {
	stateDir := filepath.Join(t.TempDir(), "tailnet-state")
	opts, err := parseOptions([]string{
		"--state-dir", stateDir,
		"--hostname", "obc-host-2",
		"--listen-port", "18420",
		"--backend-port", "28420",
	}, io.Discard)
	if err != nil {
		t.Fatalf("parseOptions() error = %v", err)
	}
	if !filepath.IsAbs(opts.stateDir) {
		t.Fatalf("stateDir = %q, want absolute path", opts.stateDir)
	}
	if opts.hostname != "obc-host-2" || opts.listenPort != 18420 || opts.backendPort != 28420 {
		t.Fatalf("unexpected options: %+v", opts)
	}
}

func TestParseOptionsRejectsInvalidArguments(t *testing.T) {
	tests := []struct {
		name string
		args []string
		want string
	}{
		{name: "missing state dir", args: nil, want: "--state-dir is required"},
		{name: "bad listen port", args: []string{"--listen-port", "0"}, want: "--listen-port"},
		{name: "bad backend port", args: []string{"--backend-port", "65536"}, want: "--backend-port"},
		{name: "bad hostname", args: []string{"--hostname", "-bad"}, want: "hostname"},
		{name: "positional argument", args: []string{"extra"}, want: "unexpected positional"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := parseOptions(test.args, io.Discard)
			if err == nil || !strings.Contains(err.Error(), test.want) {
				t.Fatalf("parseOptions() error = %v, want containing %q", err, test.want)
			}
		})
	}
}

func TestSelfTestUsesOnlyProtocolJSONL(t *testing.T) {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := runCLI([]string{"--self-test"}, strings.NewReader(""), &stdout, &stderr)
	if code != 0 {
		t.Fatalf("runCLI() code = %d, stderr = %q", code, stderr.String())
	}
	events := decodeEvents(t, stdout.String())
	if len(events) != 2 {
		t.Fatalf("events = %#v, want starting and stopped", events)
	}
	if events[0].Event != "starting" || events[1].Event != "stopped" {
		t.Fatalf("events = %#v, want starting then stopped", events)
	}
	if events[1].Message != "self-test ok" {
		t.Fatalf("stopped message = %q, want self-test ok", events[1].Message)
	}
}

func TestVersionUsesProtocolJSONL(t *testing.T) {
	var stdout bytes.Buffer
	code := runCLI([]string{"--version"}, strings.NewReader(""), &stdout, io.Discard)
	if code != 0 {
		t.Fatalf("runCLI() code = %d", code)
	}
	events := decodeEvents(t, stdout.String())
	if len(events) != 1 || events[0].Event != "stopped" || events[0].Version != version {
		t.Fatalf("events = %#v, want one version-bearing stopped event", events)
	}
}

func TestReadBootstrap(t *testing.T) {
	message, reader, atEOF, err := readBootstrap(strings.NewReader(
		`{"protocol":1,"auth_key":"secret-value","advertise_tags":["tag:openbiliclaw"]}` + "\nkeep-open",
	))
	if err != nil {
		t.Fatalf("readBootstrap() error = %v", err)
	}
	if atEOF {
		t.Fatal("readBootstrap() atEOF = true, want false")
	}
	if message.Protocol != protocolVersion || message.AuthKey != "secret-value" ||
		!slices.Equal(message.AdvertiseTags, []string{"tag:openbiliclaw"}) {
		t.Fatalf("message = %#v", message)
	}
	remainder, err := io.ReadAll(reader)
	if err != nil {
		t.Fatalf("read remainder: %v", err)
	}
	if string(remainder) != "keep-open" {
		t.Fatalf("remainder = %q", remainder)
	}
}

func TestReadBootstrapRejectsOAuthWithoutTags(t *testing.T) {
	_, _, _, err := readBootstrap(strings.NewReader(
		`{"protocol":1,"auth_key":"` + testOAuthCredential + `"}`,
	))
	if err == nil || !strings.Contains(err.Error(), "require advertise_tags") {
		t.Fatalf("readBootstrap() error = %v", err)
	}
}

func TestReadBootstrapRejectsInvalidTag(t *testing.T) {
	_, _, _, err := readBootstrap(strings.NewReader(
		`{"protocol":1,"auth_key":"` + testOAuthCredential + `","advertise_tags":["tag:1bad"]}`,
	))
	if err == nil || !strings.Contains(err.Error(), "invalid advertise tag") {
		t.Fatalf("readBootstrap() error = %v", err)
	}
}

func TestDurableOAuthCredential(t *testing.T) {
	secret := testOAuthCredential
	if got := durableOAuthCredential(secret); got != secret+"?ephemeral=false&preauthorized=true" {
		t.Fatalf("durableOAuthCredential() = %q", got)
	}
	if got := durableOAuthCredential(testAuthCredential); got != testAuthCredential {
		t.Fatalf("auth key changed to %q", got)
	}
}

func TestReadBootstrapRejectsWrongProtocol(t *testing.T) {
	_, _, _, err := readBootstrap(strings.NewReader(`{"protocol":2,"auth_key":""}`))
	if err == nil || !strings.Contains(err.Error(), "unsupported bootstrap protocol") {
		t.Fatalf("readBootstrap() error = %v", err)
	}
}

func TestBootstrapEOFStopsWithoutJoiningTailnet(t *testing.T) {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := runCLI(
		[]string{"--state-dir", filepath.Join(t.TempDir(), "tailnet")},
		strings.NewReader(`{"protocol":1,"auth_key":"unused-secret"}`),
		&stdout,
		&stderr,
	)
	if code != 0 {
		t.Fatalf("runCLI() code = %d, stderr = %q", code, stderr.String())
	}
	events := decodeEvents(t, stdout.String())
	if len(events) != 2 || events[0].Event != "starting" || events[1].Event != "stopped" {
		t.Fatalf("events = %#v, want starting then stopped", events)
	}
	for _, event := range events {
		encoded, err := json.Marshal(event)
		if err != nil {
			t.Fatalf("marshal event: %v", err)
		}
		if strings.Contains(string(encoded), "unused-secret") {
			t.Fatalf("protocol output leaked auth key: %s", encoded)
		}
	}
}

func TestSecretRedactor(t *testing.T) {
	const secret = testAuthCredential
	got := (secretRedactor{secret: secret}).text("failed with " + secret)
	if strings.Contains(got, secret) || !strings.Contains(got, "[REDACTED]") {
		t.Fatalf("redacted text = %q", got)
	}
}

func TestEnsureStateDirUsesPrivatePermissions(t *testing.T) {
	stateDir := filepath.Join(t.TempDir(), "nested", "tailnet")
	if err := ensureStateDir(stateDir); err != nil {
		t.Fatalf("ensureStateDir() error = %v", err)
	}
	info, err := os.Stat(stateDir)
	if err != nil {
		t.Fatalf("stat state dir: %v", err)
	}
	if !info.IsDir() {
		t.Fatalf("state path mode = %v, want directory", info.Mode())
	}
	if runtime.GOOS != "windows" && info.Mode().Perm() != 0o700 {
		t.Fatalf("state dir permissions = %o, want 700", info.Mode().Perm())
	}
}

func TestReverseProxyPreservesHostAndOriginAndRebuildsForwardingHeaders(t *testing.T) {
	type observedRequest struct {
		host    string
		origin  string
		headers http.Header
	}
	observed := make(chan observedRequest, 1)
	backend := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		observed <- observedRequest{
			host:    request.Host,
			origin:  request.Header.Get("Origin"),
			headers: request.Header.Clone(),
		}
		response.Header().Set("X-Backend", "openbiliclaw")
		_, _ = io.WriteString(response, "ok")
	}))
	defer backend.Close()

	proxy := mustProxyForServer(t, backend)
	front := httptest.NewServer(proxy)
	defer front.Close()

	request, err := http.NewRequest(http.MethodGet, front.URL+"/api/health?from=tailnet", nil)
	if err != nil {
		t.Fatalf("new request: %v", err)
	}
	request.Host = "openbiliclaw-host.example.ts.net:8420"
	request.Header.Set("Origin", "http://openbiliclaw-host.example.ts.net:8420")
	request.Header.Set("Forwarded", "for=203.0.113.10;proto=https")
	request.Header.Set("X-Forwarded-For", "203.0.113.10")
	request.Header.Set("X-Forwarded-Host", "evil.example")
	request.Header.Set("X-Forwarded-Proto", "https")
	request.Header.Set("X-Forwarded-Evil", "keep-me-out")
	request.Header.Set("X-Real-IP", "127.0.0.1")

	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatalf("proxy request: %v", err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(response.Body)
	if err != nil {
		t.Fatalf("read response: %v", err)
	}
	if response.StatusCode != http.StatusOK || string(body) != "ok" {
		t.Fatalf("response = %d %q", response.StatusCode, body)
	}

	got := <-observed
	if got.host != request.Host {
		t.Fatalf("backend Host = %q, want %q", got.host, request.Host)
	}
	if got.origin != request.Header.Get("Origin") {
		t.Fatalf("backend Origin = %q, want %q", got.origin, request.Header.Get("Origin"))
	}
	for _, name := range []string{"Forwarded", "X-Real-IP", "X-Forwarded-Evil"} {
		if value := got.headers.Get(name); value != "" {
			t.Fatalf("backend %s = %q, want removed", name, value)
		}
	}
	xff := got.headers.Get("X-Forwarded-For")
	if strings.Contains(xff, "203.0.113.10") || net.ParseIP(strings.TrimSpace(xff)) == nil {
		t.Fatalf("backend X-Forwarded-For = %q, want regenerated peer IP", xff)
	}
	if got.headers.Get("X-Forwarded-Host") != request.Host {
		t.Fatalf("backend X-Forwarded-Host = %q, want %q", got.headers.Get("X-Forwarded-Host"), request.Host)
	}
	if got.headers.Get("X-Forwarded-Proto") != "http" {
		t.Fatalf("backend X-Forwarded-Proto = %q, want http", got.headers.Get("X-Forwarded-Proto"))
	}
}

func TestReverseProxyTunnelsUpgradeConnection(t *testing.T) {
	backendDone := make(chan error, 1)
	backend := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if !strings.EqualFold(request.Header.Get("Connection"), "upgrade") ||
			!strings.EqualFold(request.Header.Get("Upgrade"), "websocket") {
			http.Error(response, "upgrade headers missing", http.StatusBadRequest)
			return
		}
		if request.Host != "openbiliclaw-host.example.ts.net:8420" {
			http.Error(response, "host not preserved", http.StatusBadRequest)
			return
		}
		hijacker, ok := response.(http.Hijacker)
		if !ok {
			backendDone <- fmt.Errorf("backend response does not support hijacking")
			return
		}
		connection, stream, err := hijacker.Hijack()
		if err != nil {
			backendDone <- err
			return
		}
		defer connection.Close()
		if _, err := stream.WriteString(
			"HTTP/1.1 101 Switching Protocols\r\n" +
				"Connection: Upgrade\r\n" +
				"Upgrade: websocket\r\n\r\n",
		); err != nil {
			backendDone <- err
			return
		}
		if err := stream.Flush(); err != nil {
			backendDone <- err
			return
		}
		line, err := stream.ReadString('\n')
		if err != nil {
			backendDone <- err
			return
		}
		if _, err := stream.WriteString("echo:" + line); err != nil {
			backendDone <- err
			return
		}
		backendDone <- stream.Flush()
	}))
	defer backend.Close()

	front := httptest.NewServer(mustProxyForServer(t, backend))
	defer front.Close()
	frontURL, err := url.Parse(front.URL)
	if err != nil {
		t.Fatalf("parse proxy URL: %v", err)
	}
	connection, err := net.DialTimeout("tcp", frontURL.Host, 2*time.Second)
	if err != nil {
		t.Fatalf("dial proxy: %v", err)
	}
	defer connection.Close()
	if err := connection.SetDeadline(time.Now().Add(5 * time.Second)); err != nil {
		t.Fatalf("set connection deadline: %v", err)
	}

	_, err = fmt.Fprint(connection,
		"GET /api/runtime-stream HTTP/1.1\r\n"+
			"Host: openbiliclaw-host.example.ts.net:8420\r\n"+
			"Origin: http://openbiliclaw-host.example.ts.net:8420\r\n"+
			"Connection: Upgrade\r\n"+
			"Upgrade: websocket\r\n\r\n",
	)
	if err != nil {
		t.Fatalf("write upgrade request: %v", err)
	}
	reader := bufio.NewReader(connection)
	response, err := http.ReadResponse(reader, &http.Request{Method: http.MethodGet})
	if err != nil {
		t.Fatalf("read upgrade response: %v", err)
	}
	if response.StatusCode != http.StatusSwitchingProtocols {
		t.Fatalf("upgrade status = %d, want 101", response.StatusCode)
	}
	if _, err := io.WriteString(connection, "ping\n"); err != nil {
		t.Fatalf("write tunneled payload: %v", err)
	}
	line, err := reader.ReadString('\n')
	if err != nil {
		t.Fatalf("read tunneled payload: %v", err)
	}
	if line != "echo:ping\n" {
		t.Fatalf("tunneled response = %q, want echo:ping", line)
	}
	if err := <-backendDone; err != nil {
		t.Fatalf("backend tunnel error: %v", err)
	}
}

func mustProxyForServer(t *testing.T, server *httptest.Server) *httputil.ReverseProxy {
	t.Helper()
	parsed, err := url.Parse(server.URL)
	if err != nil {
		t.Fatalf("parse backend URL: %v", err)
	}
	_, rawPort, err := net.SplitHostPort(parsed.Host)
	if err != nil {
		t.Fatalf("split backend host: %v", err)
	}
	port, err := strconv.Atoi(rawPort)
	if err != nil {
		t.Fatalf("parse backend port: %v", err)
	}
	proxy, err := newReverseProxy(port)
	if err != nil {
		t.Fatalf("newReverseProxy() error = %v", err)
	}
	return proxy
}

func decodeEvents(t *testing.T, output string) []protocolEvent {
	t.Helper()
	decoder := json.NewDecoder(strings.NewReader(output))
	events := make([]protocolEvent, 0)
	for {
		var event protocolEvent
		err := decoder.Decode(&event)
		if err == io.EOF {
			break
		}
		if err != nil {
			t.Fatalf("decode protocol output %q: %v", output, err)
		}
		if event.Protocol != protocolVersion {
			t.Fatalf("event protocol = %d, want %d", event.Protocol, protocolVersion)
		}
		events = append(events, event)
	}
	return events
}
