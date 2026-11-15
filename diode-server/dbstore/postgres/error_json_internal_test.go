package postgres

import (
	"encoding/json"
	"errors"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// unmarshallableErr fails json.Marshal the way a changeset.Error carrying a
// non-JSON json.RawMessage did before NewError quoted it.
type unmarshallableErr struct {
	Details json.RawMessage `json:"details"`
}

func (e *unmarshallableErr) Error() string {
	return "bulk plan-apply failed - Selected branch is not ready."
}

func TestIngestionLogErrorJSON_FallsBackToMessage(t *testing.T) {
	err := &unmarshallableErr{Details: json.RawMessage("Selected branch is not ready.")}
	_, mErr := json.Marshal(err)
	require.Error(t, mErr, "precondition: the error must not marshal")

	b := ingestionLogErrorJSON(err)

	var got map[string]string
	require.NoError(t, json.Unmarshal(b, &got))
	assert.Equal(t, err.Error(), got["message"])
}

func TestIngestionLogErrorJSON_MarshallableErrorUnchanged(t *testing.T) {
	type jsonErr struct {
		Message string `json:"message"`
		Code    string `json:"code"`
	}
	err := struct {
		error
		jsonErr
	}{errors.New("x"), jsonErr{"m", "c"}}
	want, mErr := json.Marshal(err)
	require.NoError(t, mErr)
	assert.Equal(t, want, ingestionLogErrorJSON(err))
}
