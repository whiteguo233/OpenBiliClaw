// Command openbiliclaw-tailnet exposes a loopback OpenBiliClaw backend only
// inside a tailnet. It embeds a userspace Tailscale node through tsnet, so it
// neither installs nor depends on the operating-system Tailscale daemon.
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	_ "tailscale.com/feature/oauthkey"
	"tailscale.com/ipn/ipnstate"
	"tailscale.com/tailcfg"
	"tailscale.com/tsnet"
)

const (
	protocolVersion    = 1
	defaultHostname    = "openbiliclaw-host"
	defaultListenPort  = 8420
	defaultBackendPort = 8420
	shutdownTimeout    = 5 * time.Second
	readHeaderTimeout  = 10 * time.Second
)

var (
	version         = "dev"
	authURLPattern  = regexp.MustCompile(`https://login\.tailscale\.com/a/[A-Za-z0-9_-]+`)
	hostnamePattern = regexp.MustCompile(`^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$`)
)

type options struct {
	stateDir    string
	hostname    string
	listenPort  int
	backendPort int
	showVersion bool
	selfTest    bool
}

type bootstrapMessage struct {
	Protocol      int      `json:"protocol"`
	AuthKey       string   `json:"auth_key"`
	AdvertiseTags []string `json:"advertise_tags,omitempty"`
}

type protocolEvent struct {
	Protocol int      `json:"protocol"`
	Event    string   `json:"event"`
	AuthURL  string   `json:"auth_url,omitempty"`
	DNSName  string   `json:"dns_name,omitempty"`
	IPs      []string `json:"ips,omitempty"`
	Port     int      `json:"port,omitempty"`
	Code     string   `json:"code,omitempty"`
	Message  string   `json:"message,omitempty"`
	Version  string   `json:"version,omitempty"`
}

type eventWriter struct {
	mu      sync.Mutex
	encoder *json.Encoder
}

func newEventWriter(w io.Writer) *eventWriter {
	encoder := json.NewEncoder(w)
	encoder.SetEscapeHTML(false)
	return &eventWriter{encoder: encoder}
}

func (w *eventWriter) emit(event protocolEvent) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	event.Protocol = protocolVersion
	return w.encoder.Encode(event)
}

type secretRedactor struct {
	secret string
}

func (r secretRedactor) text(value string) string {
	if r.secret != "" {
		value = strings.ReplaceAll(value, r.secret, "[REDACTED]")
	}
	return value
}

func (r secretRedactor) logf(logger *log.Logger, format string, args ...any) {
	logger.Print(r.text(fmt.Sprintf(format, args...)))
}

type bootstrapResult struct {
	message *bootstrapMessage
	reader  *bufio.Reader
	atEOF   bool
	err     error
}

func main() {
	os.Exit(runCLI(os.Args[1:], os.Stdin, os.Stdout, os.Stderr))
}

func runCLI(args []string, stdin io.Reader, stdout, stderr io.Writer) int {
	events := newEventWriter(stdout)
	opts, err := parseOptions(args, stderr)
	if err != nil {
		emitError(events, "invalid_arguments", err.Error(), secretRedactor{})
		emitStopped(events, "invalid arguments")
		return 2
	}

	if opts.showVersion {
		_ = events.emit(protocolEvent{Event: "stopped", Version: version, Message: "version"})
		return 0
	}

	_ = events.emit(protocolEvent{Event: "starting"})
	if opts.selfTest {
		if _, err := newReverseProxy(opts.backendPort); err != nil {
			emitError(events, "self_test_failed", err.Error(), secretRedactor{})
			emitStopped(events, "self-test failed")
			return 1
		}
		emitStopped(events, "self-test ok")
		return 0
	}

	baseCtx, stopSignals := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stopSignals()
	ctx, cancel := context.WithCancel(baseCtx)
	defer cancel()

	bootstrapCh := make(chan bootstrapResult, 1)
	go func() {
		message, reader, atEOF, readErr := readBootstrap(stdin)
		bootstrapCh <- bootstrapResult{
			message: message,
			reader:  reader,
			atEOF:   atEOF,
			err:     readErr,
		}
	}()

	var bootstrap bootstrapResult
	select {
	case <-ctx.Done():
		emitStopped(events, "shutdown before bootstrap")
		return 0
	case bootstrap = <-bootstrapCh:
	}
	if bootstrap.err != nil {
		emitError(events, "invalid_bootstrap", bootstrap.err.Error(), secretRedactor{})
		emitStopped(events, "invalid bootstrap")
		return 2
	}

	redactor := secretRedactor{secret: bootstrap.message.AuthKey}
	diagnostics := log.New(stderr, "[openbiliclaw-tailnet] ", log.LstdFlags|log.Lmsgprefix)
	if bootstrap.atEOF {
		cancel()
	} else {
		go cancelOnInputEOF(ctx, cancel, bootstrap.reader, diagnostics)
	}

	err = runTailnet(
		ctx,
		opts,
		bootstrap.message.AuthKey,
		bootstrap.message.AdvertiseTags,
		events,
		diagnostics,
		redactor,
	)
	bootstrap.message.AuthKey = ""
	bootstrap.message.AdvertiseTags = nil
	if err != nil && !errors.Is(err, context.Canceled) {
		emitError(events, "runtime_error", err.Error(), redactor)
		redactor.logf(diagnostics, "runtime error: %v", err)
		emitStopped(events, "runtime error")
		return 1
	}

	emitStopped(events, "shutdown complete")
	return 0
}

func parseOptions(args []string, stderr io.Writer) (options, error) {
	opts := options{}
	flags := flag.NewFlagSet("openbiliclaw-tailnet", flag.ContinueOnError)
	flags.SetOutput(stderr)
	flags.StringVar(&opts.stateDir, "state-dir", "", "persistent tsnet state directory")
	flags.StringVar(&opts.hostname, "hostname", defaultHostname, "tailnet node hostname")
	flags.IntVar(&opts.listenPort, "listen-port", defaultListenPort, "tailnet HTTP listen port")
	flags.IntVar(&opts.backendPort, "backend-port", defaultBackendPort, "loopback backend port")
	flags.BoolVar(&opts.showVersion, "version", false, "print the helper version as a protocol event")
	flags.BoolVar(&opts.selfTest, "self-test", false, "validate the bundled helper without joining a tailnet")
	if err := flags.Parse(args); err != nil {
		return options{}, err
	}
	if flags.NArg() != 0 {
		return options{}, fmt.Errorf("unexpected positional arguments: %s", strings.Join(flags.Args(), " "))
	}
	if err := validatePort("listen-port", opts.listenPort); err != nil {
		return options{}, err
	}
	if err := validatePort("backend-port", opts.backendPort); err != nil {
		return options{}, err
	}
	if !hostnamePattern.MatchString(opts.hostname) {
		return options{}, errors.New("hostname must be 1-63 letters, digits, or hyphens and cannot start or end with a hyphen")
	}
	if opts.showVersion || opts.selfTest {
		return opts, nil
	}
	if strings.TrimSpace(opts.stateDir) == "" {
		return options{}, errors.New("--state-dir is required")
	}
	absStateDir, err := filepath.Abs(opts.stateDir)
	if err != nil {
		return options{}, fmt.Errorf("resolve state directory: %w", err)
	}
	opts.stateDir = absStateDir
	return opts, nil
}

func validatePort(name string, value int) error {
	if value < 1 || value > 65535 {
		return fmt.Errorf("--%s must be between 1 and 65535", name)
	}
	return nil
}

func readBootstrap(input io.Reader) (*bootstrapMessage, *bufio.Reader, bool, error) {
	reader := bufio.NewReader(input)
	line, err := reader.ReadString('\n')
	atEOF := errors.Is(err, io.EOF)
	if err != nil && !atEOF {
		return nil, reader, false, fmt.Errorf("read bootstrap message: %w", err)
	}
	if strings.TrimSpace(line) == "" {
		return nil, reader, atEOF, errors.New("stdin must begin with a bootstrap JSON line")
	}
	message := &bootstrapMessage{}
	if err := json.Unmarshal([]byte(line), message); err != nil {
		return nil, reader, atEOF, fmt.Errorf("decode bootstrap message: %w", err)
	}
	if message.Protocol != protocolVersion {
		return nil, reader, atEOF, fmt.Errorf(
			"unsupported bootstrap protocol %d (expected %d)",
			message.Protocol,
			protocolVersion,
		)
	}
	for _, tag := range message.AdvertiseTags {
		if err := tailcfg.CheckTag(tag); err != nil {
			return nil, reader, atEOF, fmt.Errorf("invalid advertise tag %q: %w", tag, err)
		}
	}
	if strings.HasPrefix(message.AuthKey, "tskey-client-") && len(message.AdvertiseTags) == 0 {
		return nil, reader, atEOF, errors.New("OAuth client secrets require advertise_tags")
	}
	return message, reader, atEOF, nil
}

func cancelOnInputEOF(
	ctx context.Context,
	cancel context.CancelFunc,
	reader io.Reader,
	diagnostics *log.Logger,
) {
	_, err := io.Copy(io.Discard, reader)
	if ctx.Err() != nil {
		return
	}
	if err != nil {
		diagnostics.Printf("control stdin closed after read error: %v", err)
	}
	cancel()
}

func ensureStateDir(path string) error {
	if err := os.MkdirAll(path, 0o700); err != nil {
		return fmt.Errorf("create state directory: %w", err)
	}
	info, err := os.Lstat(path)
	if err != nil {
		return fmt.Errorf("inspect state directory: %w", err)
	}
	if info.Mode()&os.ModeSymlink != 0 || !info.IsDir() {
		return errors.New("state directory must be a real directory, not a file or symlink")
	}
	if err := os.Chmod(path, 0o700); err != nil {
		return fmt.Errorf("secure state directory permissions: %w", err)
	}
	return nil
}

func runTailnet(
	ctx context.Context,
	opts options,
	authKey string,
	advertiseTags []string,
	events *eventWriter,
	diagnostics *log.Logger,
	redactor secretRedactor,
) error {
	if err := ensureStateDir(opts.stateDir); err != nil {
		return err
	}
	if err := ctx.Err(); err != nil {
		return err
	}

	seenAuthURLs := make(map[string]struct{})
	var seenAuthURLsMu sync.Mutex
	server := &tsnet.Server{
		Dir:           opts.stateDir,
		Hostname:      opts.hostname,
		AuthKey:       durableOAuthCredential(authKey),
		AdvertiseTags: advertiseTags,
	}
	server.UserLogf = func(format string, args ...any) {
		message := redactor.text(fmt.Sprintf(format, args...))
		for _, authURL := range authURLPattern.FindAllString(message, -1) {
			seenAuthURLsMu.Lock()
			_, alreadySeen := seenAuthURLs[authURL]
			if !alreadySeen {
				seenAuthURLs[authURL] = struct{}{}
			}
			seenAuthURLsMu.Unlock()
			if !alreadySeen {
				_ = events.emit(protocolEvent{Event: "needs_login", AuthURL: authURL})
			}
		}
		diagnostics.Print(message)
	}

	status, err := server.Up(ctx)
	if err != nil {
		_ = server.Close()
		if ctx.Err() != nil {
			return ctx.Err()
		}
		return fmt.Errorf("join tailnet: %w", err)
	}

	listener, err := server.Listen("tcp", fmt.Sprintf(":%d", opts.listenPort))
	if err != nil {
		_ = server.Close()
		return fmt.Errorf("listen on tailnet port %d: %w", opts.listenPort, err)
	}

	proxy, err := newReverseProxy(opts.backendPort)
	if err != nil {
		_ = listener.Close()
		_ = server.Close()
		return err
	}
	httpServer := &http.Server{
		Handler:           proxy,
		ReadHeaderTimeout: readHeaderTimeout,
	}

	dnsName, ips := readyDetails(status, server)
	_ = events.emit(protocolEvent{
		Event:   "ready",
		DNSName: dnsName,
		IPs:     ips,
		Port:    opts.listenPort,
	})
	redactor.logf(
		diagnostics,
		"ready: hostname=%s ips=%s tailnet_port=%d backend=127.0.0.1:%d",
		dnsName,
		strings.Join(ips, ","),
		opts.listenPort,
		opts.backendPort,
	)

	serveErrCh := make(chan error, 1)
	go func() {
		serveErrCh <- httpServer.Serve(listener)
	}()

	var serveErr error
	select {
	case <-ctx.Done():
	case serveErr = <-serveErrCh:
		if !errors.Is(serveErr, http.ErrServerClosed) && !errors.Is(serveErr, net.ErrClosed) {
			redactor.logf(diagnostics, "HTTP proxy stopped unexpectedly: %v", serveErr)
		}
	}

	shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), shutdownTimeout)
	shutdownErr := httpServer.Shutdown(shutdownCtx)
	shutdownCancel()
	listenerErr := listener.Close()
	tsnetErr := server.Close()

	if serveErr != nil && !errors.Is(serveErr, http.ErrServerClosed) && !errors.Is(serveErr, net.ErrClosed) {
		return fmt.Errorf("serve tailnet HTTP proxy: %w", serveErr)
	}
	if shutdownErr != nil && !errors.Is(shutdownErr, context.DeadlineExceeded) {
		return fmt.Errorf("shutdown HTTP proxy: %w", shutdownErr)
	}
	if listenerErr != nil && !errors.Is(listenerErr, net.ErrClosed) {
		return fmt.Errorf("close tailnet listener: %w", listenerErr)
	}
	if tsnetErr != nil {
		return fmt.Errorf("close tsnet server: %w", tsnetErr)
	}
	return nil
}

func durableOAuthCredential(credential string) string {
	if strings.HasPrefix(credential, "tskey-client-") && !strings.Contains(credential, "?") {
		return credential + "?ephemeral=false&preauthorized=true"
	}
	return credential
}

func readyDetails(status *ipnstate.Status, server *tsnet.Server) (string, []string) {
	dnsName := ""
	if status != nil && status.Self != nil {
		dnsName = strings.TrimSuffix(status.Self.DNSName, ".")
	}
	ips := make([]string, 0, 2)
	if status != nil {
		for _, ip := range status.TailscaleIPs {
			if ip.IsValid() {
				ips = append(ips, ip.String())
			}
		}
	}
	if len(ips) == 0 {
		ipv4, ipv6 := server.TailscaleIPs()
		if ipv4.IsValid() {
			ips = append(ips, ipv4.String())
		}
		if ipv6.IsValid() {
			ips = append(ips, ipv6.String())
		}
	}
	return dnsName, ips
}

func newReverseProxy(backendPort int) (*httputil.ReverseProxy, error) {
	if err := validatePort("backend-port", backendPort); err != nil {
		return nil, err
	}
	target := &url.URL{
		Scheme: "http",
		Host:   net.JoinHostPort("127.0.0.1", strconv.Itoa(backendPort)),
	}
	proxy := &httputil.ReverseProxy{
		Rewrite: func(request *httputil.ProxyRequest) {
			removeForwardingHeaders(request.Out.Header)
			request.SetURL(target)
			// Preserve the external tailnet Host explicitly. OpenBiliClaw uses it
			// together with Origin for CSRF and DNS-rebinding checks.
			request.Out.Host = request.In.Host
			request.SetXForwarded()
		},
		FlushInterval: -1,
		Transport: &http.Transport{
			Proxy:             nil,
			DialContext:       (&net.Dialer{Timeout: 5 * time.Second}).DialContext,
			ForceAttemptHTTP2: false,
		},
		ErrorHandler: func(response http.ResponseWriter, request *http.Request, err error) {
			response.Header().Set("Content-Type", "application/json")
			response.WriteHeader(http.StatusBadGateway)
			_, _ = io.WriteString(response, `{"detail":"OpenBiliClaw backend unavailable"}`+"\n")
		},
	}
	return proxy, nil
}

func removeForwardingHeaders(headers http.Header) {
	for key := range headers {
		lower := strings.ToLower(key)
		if lower == "forwarded" || lower == "x-real-ip" || strings.HasPrefix(lower, "x-forwarded-") {
			headers.Del(key)
		}
	}
}

func emitError(events *eventWriter, code, message string, redactor secretRedactor) {
	_ = events.emit(protocolEvent{
		Event:   "error",
		Code:    code,
		Message: redactor.text(message),
	})
}

func emitStopped(events *eventWriter, message string) {
	_ = events.emit(protocolEvent{Event: "stopped", Message: message})
}
