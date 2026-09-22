// Copyright 2026 Alibaba Group
// Licensed under the Apache License, Version 2.0.
package helpers

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/DingTalk-Real-AI/dingtalk-workspace-cli/internal/output"
	"github.com/DingTalk-Real-AI/dingtalk-workspace-cli/internal/testseam"
	"github.com/DingTalk-Real-AI/dingtalk-workspace-cli/pkg/edition"
	"github.com/spf13/pflag"
)

type aicardCaller struct {
	calls    []string
	sent     map[string]any
	response string
	userID   string
	sendErr  error
}

func (c *aicardCaller) CallTool(_ context.Context, product, tool string, args map[string]any) (*edition.ToolResult, error) {
	c.calls = append(c.calls, product+"/"+tool)
	text := c.response
	if tool == "get_current_user_profile" {
		text = `{"result":{"userId":"DAAAAAAAAAAAiE"}}`
		if c.userID != "" {
			text = `{"result":{"userId":"` + c.userID + `"}}`
		}
	} else if tool == "get_user_info_by_user_ids" {
		text = `{"result":[{"userId":"employee-1","openDingTalkId":"DAAAAAAAAAAAiE"}]}`
	} else {
		c.sent = args
		if c.sendErr != nil {
			return nil, c.sendErr
		}
	}
	return &edition.ToolResult{Content: []edition.ContentBlock{{Type: "text", Text: text}}}, nil
}
func (*aicardCaller) Format() string { return "json" }
func (*aicardCaller) DryRun() bool   { return false }
func (*aicardCaller) Fields() string { return "" }
func (*aicardCaller) JQ() string     { return "" }

func executeAicard(t *testing.T, caller *aicardCaller, args ...string) (map[string]any, int, error) {
	t.Helper()
	testseam.Protect(t, &deps)
	InitDeps(caller)
	root := newAicardCommand()
	root.PersistentFlags().StringP("format", "f", "json", "Output format")
	root.PersistentFlags().Bool("dry-run", false, "Local validation only")
	var out, errOut bytes.Buffer
	root.SetOut(&out)
	root.SetErr(&errOut)
	root.SilenceUsage = true
	root.SilenceErrors = true
	root.SetArgs(args)
	ctx, _ := output.WithResultStore(context.Background())
	cmd, err := root.ExecuteContextC(ctx)
	if err != nil {
		return nil, -1, err
	}
	code, _, err := output.EmitStoredResult(cmd)
	if err != nil {
		return nil, code, err
	}
	raw := out.Bytes()
	if len(raw) == 0 {
		raw = errOut.Bytes()
	}
	var result map[string]any
	err = json.Unmarshal(raw, &result)
	return result, code, err
}

func aicardTestFile(t *testing.T, contents string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "中文 card.json")
	if err := os.WriteFile(path, []byte(contents), 0600); err != nil {
		t.Fatal(err)
	}
	return path
}

const aicardTestSnapshot = `[{"version":"v1.0","createSurface":{"surfaceId":"s","catalogId":"https://dingtalk.com/card/a2ui/catalogs/public/catalog.json","dataModel":{}}},{"version":"v1.0","updateComponents":{"surfaceId":"s","components":[{"id":"root","component":"Text","text":"中文 \"引号\"\n换行"}]}}]`

func TestAicardCommandSurface(t *testing.T) {
	root := newAicardCommand()
	want := map[string]int{"lint": 5, "explain": 1, "preview": 2}
	for _, cmd := range root.Commands() {
		n, ok := want[cmd.Name()]
		if !ok {
			t.Fatal(cmd.Name())
		}
		if cmd.Flags().NFlag() != 0 {
			t.Fatal("flags unexpectedly changed")
		}
		count := 0
		cmd.Flags().VisitAll(func(_ *pflag.Flag) { count++ })
		if count != n {
			t.Fatalf("%s flags %d != %d", cmd.Name(), count, n)
		}
		delete(want, cmd.Name())
	}
	if len(want) > 0 {
		t.Fatal(want)
	}
}

func TestAicardPositionalsAndFlags(t *testing.T) {
	for _, args := range [][]string{{"explain"}, {"lint", "card.json"}, {"preview", "x"}, {"lint", "--strict"}, {"lint", "--explain", "Text"}, {"lint", "--lock-file", "x"}, {"preview", "--open-dingtalk-id", "x"}} {
		t.Run(strings.Join(args, " "), func(t *testing.T) {
			_, _, err := executeAicard(t, &aicardCaller{}, args...)
			if err == nil {
				t.Fatal("unexpected accepted arguments")
			}
		})
	}
	for _, args := range [][]string{{"lint"}, {"lint", "--self-check", "--file", "x"}, {"lint", "--self-check", "--emit"}, {"lint", "--file", "x", "--emit", "--fragment"}} {
		_, code, err := executeAicard(t, &aicardCaller{}, args...)
		if err != nil || code == 0 {
			t.Fatalf("%v code=%d err=%v", args, code, err)
		}
	}
}

func TestAicardLocalCommands(t *testing.T) {
	caller := &aicardCaller{}
	for _, name := range []string{"Tabs", "promptText", "ColorToken", "common_red1_color", "Action"} {
		result, code, err := executeAicard(t, caller, "explain", name)
		if err != nil || code != 0 {
			t.Fatalf("%s %v %d", name, err, code)
		}
		if result["data"].(map[string]any)["name"] != name {
			t.Fatal(result)
		}
	}
	result, code, err := executeAicard(t, caller, "explain", "Tabss")
	if err != nil || code == 0 || result["error"].(map[string]any)["details"].(map[string]any)["kind"] != "unknown" {
		t.Fatalf("%v %d %v", result, code, err)
	}
	result, code, err = executeAicard(t, caller, "lint", "--self-check")
	if err != nil || code != 0 || result["data"].(map[string]any)["ready"] != true {
		t.Fatalf("%v %d %v", result, code, err)
	}
	if len(caller.calls) > 0 {
		t.Fatal("local command contacted server")
	}
}

func TestAicardLintEncodingAndFailure(t *testing.T) {
	file := aicardTestFile(t, aicardTestSnapshot)
	result, code, err := executeAicard(t, &aicardCaller{}, "lint", "--file", file, "--emit")
	if err != nil || code != 0 {
		t.Fatalf("%v code=%d err=%v", result, code, err)
	}
	data := result["data"].(map[string]any)
	for _, item := range data["a2uiMessages"].([]any) {
		var msg map[string]any
		if err := json.Unmarshal([]byte(item.(string)), &msg); err != nil {
			t.Fatal(err)
		}
	}
	for _, input := range []string{`{"id":"x","component":"Text","text":1}`, `{"id":"x","component":"Text","text":"t","bogus":1}`, `{"bad":`} {
		file := aicardTestFile(t, input)
		result, code, err := executeAicard(t, &aicardCaller{}, "lint", "--file", file, "--fragment")
		if err != nil || code == 0 || result["error"] == nil {
			t.Fatalf("%v code=%d err=%v", result, code, err)
		}
	}
}

func TestAicardPreviewDryRunAndNoSilentInitialization(t *testing.T) {
	caller := &aicardCaller{}
	file := aicardTestFile(t, aicardTestSnapshot)
	before, _ := os.ReadFile(file)
	result, code, err := executeAicard(t, caller, "preview", "--file", file, "--dry-run")
	if err != nil || code != 0 || result["data"].(map[string]any)["executed"] != false {
		t.Fatalf("%v %d %v", result, code, err)
	}
	after, _ := os.ReadFile(file)
	if !bytes.Equal(before, after) || len(caller.calls) > 0 {
		t.Fatal("dry run mutated state")
	}
	file = aicardTestFile(t, `[{"version":"v1.0","updateComponents":{"surfaceId":"s","components":[]}}]`)
	_, code, err = executeAicard(t, caller, "preview", "--file", file)
	if err != nil || code == 0 || len(caller.calls) > 0 {
		t.Fatalf("missing create: %d %v %v", code, err, caller.calls)
	}
}

func TestAicardPreviewRequestAndReceipt(t *testing.T) {
	file := aicardTestFile(t, aicardTestSnapshot)
	for _, response := range []string{`{"success":true,"result":{"bizId":"b","openTaskId":"t"}}`, `{"success":false,"errorCode":"DENIED"}`, `{}`} {
		caller := &aicardCaller{response: response}
		result, code, err := executeAicard(t, caller, "preview", "--file", file)
		if err != nil {
			t.Fatal(err)
		}
		if len(caller.calls) != 2 || caller.calls[1] != "im/create_and_send_a2ui_card" {
			t.Fatal(caller.calls)
		}
		if caller.sent["receiverOpenDingTalkId"] != "DAAAAAAAAAAAiE" {
			t.Fatal(caller.sent)
		}
		if strings.Contains(response, `"success":true`) {
			if code != 0 || result["outcome"] != "success" {
				t.Fatal(result)
			}
			data := result["data"].(map[string]any)
			if data["requestAccepted"] != true || data["deliveryVerified"] != false || data["renderingVerified"] != false {
				t.Fatal(data)
			}
			if receipt := data["receipt"].(map[string]any); receipt["result"].(map[string]any)["openTaskId"] != "t" {
				t.Fatal("card task ID was not preserved in the original receipt", receipt)
			}
			if meta, ok := result["meta"].(map[string]any); ok && meta["operation"] != nil {
				t.Fatal("card task ID must not produce a message-send status command", meta)
			}
		} else if code == 0 {
			t.Fatal("failure or ambiguous receipt was accepted")
		}
	}
}

func TestAicardPreviewResolvesSelfAndPreservesUnknownDelivery(t *testing.T) {
	file := aicardTestFile(t, aicardTestSnapshot)
	caller := &aicardCaller{userID: "employee-1", response: `{"success":true,"result":{"cardInstanceId":"card-1"}}`}
	result, code, err := executeAicard(t, caller, "preview", "--file", file)
	if err != nil || code != 0 || result["outcome"] != "success" {
		t.Fatalf("%v code=%d err=%v", result, code, err)
	}
	if strings.Join(caller.calls, ",") != "contact/get_current_user_profile,contact/get_user_info_by_user_ids,im/create_and_send_a2ui_card" || caller.sent["receiverOpenDingTalkId"] != "DAAAAAAAAAAAiE" {
		t.Fatalf("identity resolution or recipient mismatch: %v %v", caller.calls, caller.sent)
	}
	var want []any
	if err := json.Unmarshal([]byte(aicardTestSnapshot), &want); err != nil {
		t.Fatal(err)
	}
	for i, raw := range caller.sent["a2uiMessages"].([]string) {
		var message any
		if err := json.Unmarshal([]byte(raw), &message); err != nil {
			t.Fatal(err)
		}
		gotJSON, _ := json.Marshal(message)
		wantJSON, _ := json.Marshal(want[i])
		if !bytes.Equal(gotJSON, wantJSON) {
			t.Fatal("send encoding changed the original message")
		}
	}
	caller = &aicardCaller{sendErr: errors.New("simulated connection loss")}
	result, code, err = executeAicard(t, caller, "preview", "--file", file)
	if err != nil || code == 0 || len(caller.calls) != 2 {
		t.Fatalf("%v code=%d err=%v calls=%v", result, code, err, caller.calls)
	}
	details := result["error"].(map[string]any)["details"].(map[string]any)
	if details["requestId"] != caller.sent["requestId"] || details["bizCardId"] != caller.sent["bizCardId"] || details["retryAutomatically"] != false {
		t.Fatal("uncertain delivery identifier was lost or retried automatically", details)
	}
}

func TestAicardBatchAndExplicitPreflight(t *testing.T) {
	caller := &aicardCaller{}
	result, code, err := executeAicard(t, caller, "explain", "Text", "Tabs", "--compact")
	if err != nil || code != 0 || result["data"].(map[string]any)["kind"] != "bundle" {
		t.Fatal(result, code, err)
	}
	path := aicardTestFile(t, strings.Replace(aicardTestSnapshot, `,"dataModel":{}`, "", 1))
	result, code, err = executeAicard(t, caller, "lint", "--file", path, "--preflight", "new-card")
	if err != nil || code == 0 {
		t.Fatal(result, code, err)
	}
	details := result["error"].(map[string]any)["details"].(map[string]any)
	if details["valid"] != true || details["preflight"].(map[string]any)["valid"] != false {
		t.Fatal(details)
	}
	if len(caller.calls) != 0 {
		t.Fatal("offline query and lint must not send", caller.calls)
	}
}
