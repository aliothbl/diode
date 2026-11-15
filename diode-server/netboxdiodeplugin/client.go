package netboxdiodeplugin

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/hashicorp/go-retryablehttp"
	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	"golang.org/x/oauth2"
	"golang.org/x/oauth2/clientcredentials"
	"golang.org/x/time/rate"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"

	"github.com/netboxlabs/diode/diode-server/authutil"
	diodeErrors "github.com/netboxlabs/diode/diode-server/errors"
	"github.com/netboxlabs/diode/diode-server/reconciler/changeset"
	"github.com/netboxlabs/diode/diode-server/telemetry"
)

const (
	// SDKName is the name of the SDK
	SDKName = "netbox-diode-plugin-sdk-go"

	// SDKVersion is the version of the SDK
	SDKVersion = "0.1.0" // TODO: consider making this same as diode-reconciler version

	// TLSSkipVerifyEnvVarName is the environment variable name for NetBox Diode plugin TLS verification
	TLSSkipVerifyEnvVarName = "NETBOX_DIODE_PLUGIN_SKIP_TLS_VERIFY"

	// TimeoutSecondsEnvVarName is the environment variable name for the NetBox Diode plugin HTTP timeout
	TimeoutSecondsEnvVarName = "NETBOX_DIODE_PLUGIN_API_TIMEOUT_SECONDS"

	// defaultHTTPTimeoutSeconds is the default HTTP timeout
	defaultHTTPTimeoutSeconds = 5

	// ApplyTimeoutSecondsEnvVarName is the environment variable name for the
	// response timeout of /bulk-plan-apply, which writes to NetBox.
	ApplyTimeoutSecondsEnvVarName = "NETBOX_DIODE_PLUGIN_APPLY_TIMEOUT_SECONDS"

	// defaultApplyTimeoutSeconds is the default /bulk-plan-apply response
	// timeout. A batch is applied serially inside one request, so the wait
	// scales with the batch size; the plan/read timeout (5s) is far too short
	// for it and expiring it does not stop the work on the NetBox side.
	defaultApplyTimeoutSeconds = 120

	// NetBoxBranchHeader is an HTTP header that indicates the NetBox branch to target
	NetBoxBranchHeader = "X-NetBox-Branch"

	// NetBoxBranchParam is a query parameter that indicates the NetBox branch to target
	NetBoxBranchParam = "_branch"
)

// ErrInvalidTimeout is an error for invalid timeout value
var ErrInvalidTimeout = errors.New("invalid timeout value")

// ErrDefaultBranchNotFound is returned when the default branch is not found (e.g. no endpoint available in older plugin versions)
var ErrDefaultBranchNotFound = errors.New("default branch not found")

// ChangeSet represents a change set
type ChangeSet struct {
	ID      string   `json:"id"`
	Changes []Change `json:"changes"`
	Branch  *Branch  `json:"branch"`
}

// Change represents a change
type Change struct {
	ID                 string          `json:"id"`
	ChangeType         string          `json:"change_type"`
	ObjectType         string          `json:"object_type"`
	ObjectID           *int            `json:"object_id,omitempty"`
	RefID              *string         `json:"ref_id,omitempty"`
	ObjectVersion      *int            `json:"object_version,omitempty"`
	Data               json.RawMessage `json:"data"`
	Before             json.RawMessage `json:"before,omitempty"`
	ObjectPrimaryValue string          `json:"object_primary_value,omitempty"`
	NewRefs            []string        `json:"new_refs,omitempty"`
	// TagsResolved names every tag the object holds once the change applies,
	// as [{"ref", "name", "slug"}] where ref is the tag as Before and Data
	// carry it (a primary key or a new_object ref). Absent from plugins that
	// predate it and on objects without tags.
	TagsResolved json.RawMessage `json:"tags_resolved,omitempty"`
}

// Branch represents a NetBox branch details
type Branch struct {
	ID   string `json:"id"`
	Name string `json:"name"`
}

// Client is a NetBox Diode plugin client
type Client struct {
	logger *slog.Logger
	http   *http.Client
	// applyHTTP serves /bulk-plan-apply. It has its own response timeout and
	// never retries a request NetBox may already be applying (see
	// applyRetryPolicy).
	applyHTTP *http.Client
	baseURL   *url.URL
	userAgent string
	limiter   *rate.Limiter
}

// headerRoundTripper adds common headers to all requests
type headerRoundTripper struct {
	transport http.RoundTripper
}

// RoundTrip adds common headers to all requests
func (rt *headerRoundTripper) RoundTrip(req *http.Request) (*http.Response, error) {
	reqClone := req.Clone(req.Context())

	reqClone.Header.Set("User-Agent", SDKName+"/"+SDKVersion)
	reqClone.Header.Set("Content-Type", "application/json")
	reqClone.Header.Set("Accept", "application/json")

	return rt.transport.RoundTrip(reqClone)
}

// NewHTTPTransport creates a http Transport Layer
func NewHTTPTransport() *http.Transport {
	return &http.Transport{
		Proxy: http.ProxyFromEnvironment,
		DialContext: (&net.Dialer{
			Timeout:   30 * time.Second,
			KeepAlive: 30 * time.Second,
		}).DialContext,
		ForceAttemptHTTP2:     true,
		MaxIdleConns:          100,
		MaxIdleConnsPerHost:   100,
		IdleConnTimeout:       90 * time.Second,
		TLSHandshakeTimeout:   10 * time.Second,
		ExpectContinueTimeout: 1 * time.Second,
		TLSClientConfig: &tls.Config{
			InsecureSkipVerify: skipTLS(),
		},
	}
}

// ClientOptions represents the options for creating a new NetBox Diode plugin client
type ClientOptions struct {
	// BaseURL is the base URL of the NetBox Diode plugin API
	BaseURL string

	// ClientID is the client ID for the NetBox Diode plugin API
	ClientID string
	// ClientSecret is the client secret for the NetBox Diode plugin API
	ClientSecret string
	// TokenURL is the URL of the token endpoint
	TokenURL string
	// TokenEndpointParams are extra parameters provided to the token endpoint
	TokenEndpointParams url.Values

	// Logger is the logger for the NetBox Diode plugin client
	Logger *slog.Logger

	// RateLimitRPS is the rate limit for the NetBox Diode plugin client
	RateLimitRPS int
	// RateLimitBurstRPS is the rate limit burst for the NetBox Diode plugin client
	RateLimitBurstRPS int
	// MaxRetries is the maximum number of retries for the NetBox Diode plugin client
	MaxRetries int
}

// NewClient creates a new NetBox Diode plugin client
func NewClient(options ClientOptions) (*Client, error) {
	u, err := url.Parse(options.BaseURL)
	if err != nil {
		return nil, err
	}

	if len(options.ClientID) == 0 || len(options.ClientSecret) == 0 {
		return nil, fmt.Errorf("client ID or secret not provided")
	}

	if len(options.TokenURL) == 0 {
		return nil, fmt.Errorf("token URL not provided")
	}

	timeout, err := httpTimeout()
	if err != nil {
		return nil, err
	}

	if options.RateLimitRPS <= 0 || options.RateLimitBurstRPS <= 0 {
		return nil, fmt.Errorf("invalid rate limit values: %d %d", options.RateLimitRPS, options.RateLimitBurstRPS)
	}

	t, err := url.Parse(options.TokenURL)
	if err != nil {
		return nil, err
	}

	applyTimeout, err := applyHTTPTimeout()
	if err != nil {
		return nil, err
	}

	oauthConfig := &clientcredentials.Config{
		ClientID:       options.ClientID,
		ClientSecret:   options.ClientSecret,
		TokenURL:       t.String(),
		Scopes:         []string{authutil.ScopeNetBoxRead, authutil.ScopeNetBoxWrite},
		EndpointParams: options.TokenEndpointParams,
	}
	// One token source for both clients, so they share the cached token.
	tokenSource := oauthConfig.TokenSource(context.Background())

	newRetryableClient := func(responseTimeout time.Duration, checkRetry retryablehttp.CheckRetry, errorHandler retryablehttp.ErrorHandler) *http.Client {
		baseTransport := NewHTTPTransport()
		// Use per-request timeout on the transport instead of http.Client.Timeout.
		// Client.Timeout cancels the request context, which prevents retryablehttp
		// from retrying. ResponseHeaderTimeout only limits the server response wait
		// per attempt, allowing retries on timeout errors where checkRetry allows.
		baseTransport.ResponseHeaderTimeout = responseTimeout

		oauthTransport := &oauth2.Transport{
			Source: tokenSource,
			Base:   otelhttp.NewTransport(&headerRoundTripper{transport: baseTransport}),
		}

		rhttp := retryablehttp.NewClient()
		rhttp.HTTPClient = &http.Client{Transport: oauthTransport}
		rhttp.RetryMax = options.MaxRetries
		rhttp.RetryWaitMin = 150 * time.Millisecond
		rhttp.RetryWaitMax = 2 * time.Second
		rhttp.Logger = options.Logger
		rhttp.CheckRetry = checkRetry
		rhttp.ErrorHandler = errorHandler
		return rhttp.StandardClient()
	}

	client := &Client{
		logger: options.Logger,
		http:   newRetryableClient(timeout, retryablehttp.DefaultRetryPolicy, nil),
		// The apply client hands the last response back once the retries are
		// spent, instead of a generic "giving up" error, so BulkPlanApply can
		// still read a 503/429 and its Retry-After (see UnavailableError).
		applyHTTP: newRetryableClient(applyTimeout, applyRetryPolicy, retryablehttp.PassthroughErrorHandler),
		baseURL:   u,
		userAgent: fmt.Sprintf("%s/%s", SDKName, SDKVersion),
		limiter:   rate.NewLimiter(rate.Limit(options.RateLimitRPS), options.RateLimitBurstRPS),
	}

	return client, nil
}

func skipTLS() bool {
	skipTLS, ok := os.LookupEnv(TLSSkipVerifyEnvVarName)
	if !ok {
		return false
	}
	skip, err := strconv.ParseBool(skipTLS)
	if err != nil {
		return false
	}
	return skip
}

func httpTimeout() (time.Duration, error) {
	timeoutSecondsStr, ok := os.LookupEnv(TimeoutSecondsEnvVarName)
	if !ok || len(timeoutSecondsStr) == 0 {
		return defaultHTTPTimeoutSeconds * time.Second, nil
	}

	timeout, err := strconv.Atoi(timeoutSecondsStr)
	if err != nil || timeout <= 0 {
		return 0, ErrInvalidTimeout
	}
	return time.Duration(timeout) * time.Second, nil
}

func applyHTTPTimeout() (time.Duration, error) {
	s, ok := os.LookupEnv(ApplyTimeoutSecondsEnvVarName)
	if !ok || len(s) == 0 {
		return defaultApplyTimeoutSeconds * time.Second, nil
	}
	timeout, err := strconv.Atoi(s)
	if err != nil || timeout <= 0 {
		return 0, ErrInvalidTimeout
	}
	return time.Duration(timeout) * time.Second, nil
}

// applyRetryPolicy is the retry policy for /bulk-plan-apply, which is not
// idempotent: it creates objects. Once the request has reached NetBox, a
// timeout or a dropped connection does not mean it was not applied; the
// worker keeps applying the batch. Resending it then races the first request
// and can create the same objects twice (NetBox allows duplicate IP
// addresses, for one). So it retries only when the request provably did not
// start: the connection could not be established, or the server answered
// 429/503 (rejected before processing, e.g. granian backpressure).
func applyRetryPolicy(ctx context.Context, resp *http.Response, err error) (bool, error) {
	if ctx.Err() != nil {
		return false, ctx.Err()
	}
	if err != nil {
		var opErr *net.OpError
		if errors.As(err, &opErr) && opErr.Op == "dial" {
			return true, nil
		}
		return false, err
	}
	if resp.StatusCode == http.StatusTooManyRequests || resp.StatusCode == http.StatusServiceUnavailable {
		return true, nil
	}
	return false, nil
}

func (c *Client) waitForRateLimit(ctx context.Context) (err error) {
	_, span := telemetry.StartSpan(ctx, telemetry.SpanRateLimiterWait)
	defer func() { telemetry.End(span, err) }()
	return c.limiter.Wait(ctx)
}

// NetBoxAPI is the interface for the NetBox Diode plugin API
type NetBoxAPI interface {
	// BulkPlan generates diffs for multiple entities in a single request
	BulkPlan(context.Context, BulkPlanRequest) (*BulkPlanResponse, error)

	// BulkPlanApply combines plan + apply for a batch of entities in a single request
	BulkPlanApply(context.Context, BulkPlanApplyRequest) (*BulkPlanApplyResponse, error)

	// GetDefaultBranch gets the default branch from NetBox plugin settings
	GetDefaultBranch(context.Context) (*Branch, error)
}

func protoToJSON(proto proto.Message) (json.RawMessage, error) {
	marshaler := protojson.MarshalOptions{
		UseProtoNames: true,
	}
	jsonBytes, err := marshaler.Marshal(proto)
	if err != nil {
		return nil, err
	}
	return json.RawMessage(jsonBytes), nil
}

// BulkPlanEntity represents a single entity in a bulk plan request
type BulkPlanEntity struct {
	ID         string          `json:"id"`
	ObjectType string          `json:"object_type"`
	BranchID   string          `json:"-"`
	Entity     proto.Message   `json:"-"`
	EntityJSON json.RawMessage `json:"entity"`
}

// BulkPlanRequest represents a bulk plan request
type BulkPlanRequest struct {
	Entities []BulkPlanEntity `json:"entities"`
	BranchID string           `json:"-"`
}

// BulkPlanResult represents a single entity result in a bulk plan response
type BulkPlanResult struct {
	ID        string          `json:"id"`
	ChangeSet *ChangeSet      `json:"change_set"`
	Errors    json.RawMessage `json:"errors"`
}

// BulkPlanResponse represents the response from the bulk plan endpoint
type BulkPlanResponse struct {
	Results []BulkPlanResult `json:"results"`
}

// BulkPlan generates diffs for multiple entities in a single request
func (c *Client) BulkPlan(ctx context.Context, payload BulkPlanRequest) (*BulkPlanResponse, error) {
	endpointURL, err := url.Parse(fmt.Sprintf("%s/bulk-plan/", c.baseURL.String()))
	if err != nil {
		return nil, err
	}

	for i := range payload.Entities {
		if payload.Entities[i].Entity != nil {
			payload.Entities[i].EntityJSON, err = protoToJSON(payload.Entities[i].Entity)
			if err != nil {
				return nil, fmt.Errorf("marshaling entity %s: %w", payload.Entities[i].ID, err)
			}
		}
	}

	reqBody, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}

	if err := c.waitForRateLimit(ctx); err != nil {
		return nil, err
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpointURL.String(), bytes.NewBuffer(reqBody))
	if err != nil {
		return nil, err
	}

	branchID := strings.TrimSpace(payload.BranchID)
	if branchID != "" {
		req.Header.Set(NetBoxBranchHeader, branchID)
	}

	resp, err := c.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer func() {
		if closeErr := resp.Body.Close(); closeErr != nil {
			c.logger.Warn("failed to close response body", "error", closeErr)
		}
	}()

	respBytes, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read response body: %w", err)
	}

	c.logger.Debug("bulk plan", "statusCode", resp.StatusCode, "response", string(respBytes))

	if resp.StatusCode >= http.StatusBadRequest {
		return nil, changeset.NewError("bulk plan failed", diodeErrors.ErrCodeOpsGenerateDiff, respBytes)
	}

	var bulkPlanResponse BulkPlanResponse
	if err = json.Unmarshal(respBytes, &bulkPlanResponse); err != nil {
		return nil, fmt.Errorf("failed to unmarshal response body: %w", err)
	}

	return &bulkPlanResponse, nil
}

// BulkPlanApplyRequest is the payload for /bulk-plan-apply. Mirrors BulkPlanRequest.
type BulkPlanApplyRequest struct {
	Entities []BulkPlanEntity `json:"entities"`
	BranchID string           `json:"-"`
}

// BulkPlanApplyErrors holds per-phase errors for a single entity in a /bulk-plan-apply response.
// The plan and apply phases are reported separately so callers can attribute failures.
type BulkPlanApplyErrors struct {
	Plan  json.RawMessage `json:"plan,omitempty"`
	Apply json.RawMessage `json:"apply,omitempty"`
}

// BulkPlanApplyResult is the per-entity result in a /bulk-plan-apply response.
// ChangeSet is always populated when the plan phase succeeded, regardless of apply outcome,
// so the caller can persist it for audit/retry.
type BulkPlanApplyResult struct {
	ID        string               `json:"id"`
	ChangeSet *ChangeSet           `json:"change_set"`
	Errors    *BulkPlanApplyErrors `json:"errors"`
}

// BulkPlanApplyResponse is the envelope returned from /bulk-plan-apply.
type BulkPlanApplyResponse struct {
	Results []BulkPlanApplyResult `json:"results"`
}

// BulkPlanApply runs plan + apply for a batch of entities in a single request.
// HTTP 200 means every entity succeeded both phases; HTTP 207 means at least one
// entity had a plan or apply error (per-result errors describe which). Both are
// treated as a successful HTTP call here — the caller inspects per-result Errors.
func (c *Client) BulkPlanApply(ctx context.Context, payload BulkPlanApplyRequest) (*BulkPlanApplyResponse, error) {
	endpointURL, err := url.Parse(fmt.Sprintf("%s/bulk-plan-apply/", c.baseURL.String()))
	if err != nil {
		return nil, err
	}

	for i := range payload.Entities {
		if payload.Entities[i].Entity != nil {
			payload.Entities[i].EntityJSON, err = protoToJSON(payload.Entities[i].Entity)
			if err != nil {
				return nil, fmt.Errorf("marshaling entity %s: %w", payload.Entities[i].ID, err)
			}
		}
	}

	reqBody, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}

	if err := c.waitForRateLimit(ctx); err != nil {
		return nil, err
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpointURL.String(), bytes.NewBuffer(reqBody))
	if err != nil {
		return nil, err
	}

	branchID := strings.TrimSpace(payload.BranchID)
	if branchID != "" {
		req.Header.Set(NetBoxBranchHeader, branchID)
	}
	// One connection per apply call. A server that refuses a request before
	// reading its body (granian's own 503 under backpressure; netbox-branching
	// before it drained the body) can leave the unread bytes on a keep-alive
	// connection, and the retry sent on it then never gets an answer: the
	// batch hangs until the apply timeout and is recorded as failed. An apply
	// takes seconds, so a fresh connection costs nothing that matters.
	req.Close = true

	resp, err := c.applyHTTP.Do(req)
	if err != nil {
		return nil, err
	}
	defer func() {
		if closeErr := resp.Body.Close(); closeErr != nil {
			c.logger.Warn("failed to close response body", "error", closeErr)
		}
	}()

	respBytes, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read response body: %w", err)
	}

	c.logger.Debug("bulk plan-apply", "statusCode", resp.StatusCode, "response", string(respBytes))

	if uErr := unavailableError(resp, respBytes); uErr != nil {
		return nil, uErr
	}
	if resp.StatusCode >= http.StatusBadRequest {
		return nil, changeset.NewError("bulk plan-apply failed", diodeErrors.ErrCodeOpsApplyChangeSet, respBytes)
	}

	var bulkPlanApplyResponse BulkPlanApplyResponse
	if err = json.Unmarshal(respBytes, &bulkPlanApplyResponse); err != nil {
		return nil, fmt.Errorf("failed to unmarshal response body: %w", err)
	}

	return &bulkPlanApplyResponse, nil
}

// GetDefaultBranchResponse represents the response from the default-branch endpoint
type GetDefaultBranchResponse struct {
	Branch *Branch `json:"branch"`
}

// GetDefaultBranch gets the default branch from NetBox plugin settings
func (c *Client) GetDefaultBranch(ctx context.Context) (*Branch, error) {
	endpointURL, err := url.Parse(fmt.Sprintf("%s/default-branch/", c.baseURL.String()))
	if err != nil {
		return nil, err
	}

	if err := c.waitForRateLimit(ctx); err != nil {
		return nil, err
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpointURL.String(), nil)
	if err != nil {
		return nil, err
	}

	resp, err := c.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer func() {
		if closeErr := resp.Body.Close(); closeErr != nil {
			c.logger.Warn("failed to close response body", "error", closeErr)
		}
	}()

	respBytes, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read response body %w", err)
	}

	// Log response body only for successful responses to avoid noise from 404s (endpoint not available on older plugin versions)
	if resp.StatusCode < http.StatusBadRequest {
		c.logger.Debug("get default branch", "statusCode", resp.StatusCode, "response", string(respBytes))
	} else {
		c.logger.Debug("get default branch", "statusCode", resp.StatusCode)
	}

	if resp.StatusCode >= http.StatusBadRequest {
		// Return sentinel error for 404 to allow callers to cache and handle gracefully
		if resp.StatusCode == http.StatusNotFound {
			return nil, ErrDefaultBranchNotFound
		}
		return nil, fmt.Errorf("get default branch failed with status %d: %s", resp.StatusCode, string(respBytes))
	}

	var defaultBranchResponse GetDefaultBranchResponse
	if err = json.Unmarshal(respBytes, &defaultBranchResponse); err != nil {
		return nil, fmt.Errorf("failed to unmarshal response body %w", err)
	}

	return defaultBranchResponse.Branch, nil
}

// UnavailableError means NetBox refused a /bulk-plan-apply before processing
// it, so nothing in the batch was applied and the batch can be sent again
// later instead of being recorded as failed.
//
//   - 503 / 429: transient (Retryable). netbox-branching answers 503 with
//     Retry-After while the selected branch is syncing, merging, migrating,
//     ... ; granian answers 503 under backpressure; 429 is rate limiting.
//   - 409 with {"retryable": false}: netbox-branching's answer for a branch
//     that will not become usable on its own (merged, archived, failed,
//     pending migrations). Not Retryable: the default branch setting needs
//     an operator.
type UnavailableError struct {
	StatusCode int
	// RetryAfter is the server's Retry-After, or zero when it sent none.
	RetryAfter time.Duration
	Retryable  bool
	Body       string
}

func (e *UnavailableError) Error() string {
	return fmt.Sprintf("bulk plan-apply refused by NetBox: HTTP %d (retryable=%t, retry-after=%s): %s",
		e.StatusCode, e.Retryable, e.RetryAfter, e.Body)
}

// unavailableError classifies a /bulk-plan-apply response; nil when the
// response is not a refusal-before-processing.
func unavailableError(resp *http.Response, body []byte) *UnavailableError {
	switch resp.StatusCode {
	case http.StatusServiceUnavailable, http.StatusTooManyRequests:
		return &UnavailableError{
			StatusCode: resp.StatusCode,
			RetryAfter: retryAfter(resp.Header.Get("Retry-After")),
			Retryable:  true,
			Body:       string(body),
		}
	case http.StatusConflict:
		// Only netbox-branching's refusal of an unusable branch; any other
		// 409 stays an ordinary apply failure.
		var refusal struct {
			Retryable *bool `json:"retryable"`
		}
		if json.Unmarshal(body, &refusal) == nil && refusal.Retryable != nil && !*refusal.Retryable {
			return &UnavailableError{StatusCode: resp.StatusCode, Retryable: false, Body: string(body)}
		}
	}
	return nil
}

// retryAfter parses a Retry-After header given in seconds (the form
// netbox-branching sends); other forms yield zero.
func retryAfter(v string) time.Duration {
	secs, err := strconv.Atoi(strings.TrimSpace(v))
	if err != nil || secs < 0 {
		return 0
	}
	return time.Duration(secs) * time.Second
}
