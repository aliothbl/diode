package netboxdiodeplugin_test

import (
	"context"
	"io"
	"net/http"
	"sync"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// A retried /bulk-plan-apply (after a 503) must carry the same body as the
// first attempt. An empty retry body with the original Content-Length makes
// the server wait for bytes that never come, and the client then times out.
func TestBulkPlanApplyRetrySendsTheSameBody(t *testing.T) {
	cleanUpEnvVars()
	var mu sync.Mutex
	var bodies []string
	client := newApplyTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		mu.Lock()
		bodies = append(bodies, string(b))
		n := len(bodies)
		mu.Unlock()
		if n == 1 {
			w.Header().Set("Retry-After", "1")
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		_, _ = w.Write([]byte(`{"results":[{"id":"1","change_set":{"id":"cs","changes":[]}}]}`))
	}, nil)

	_, err := client.BulkPlanApply(context.Background(), applyRequest())
	require.NoError(t, err)
	require.Len(t, bodies, 2)
	assert.NotEmpty(t, bodies[0])
	assert.Equal(t, bodies[0], bodies[1], "the retry must resend the request body")
}

// Every /bulk-plan-apply attempt asks for its own connection, so a retry
// never goes out on a connection a refusal may have left unusable.
func TestBulkPlanApplyDoesNotReuseConnections(t *testing.T) {
	cleanUpEnvVars()
	var mu sync.Mutex
	var closes []bool
	client := newApplyTestClient(t, func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		closes = append(closes, r.Close)
		n := len(closes)
		mu.Unlock()
		if n == 1 {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		_, _ = w.Write([]byte(`{"results":[{"id":"1","change_set":{"id":"cs","changes":[]}}]}`))
	}, nil)

	_, err := client.BulkPlanApply(context.Background(), applyRequest())
	require.NoError(t, err)
	assert.Equal(t, []bool{true, true}, closes, "each attempt must send Connection: close")
}
