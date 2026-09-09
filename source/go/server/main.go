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
    "os"
    "os/signal"
    "sync"
    "syscall"
)

const (
    ServiceName = "xyz.openbmc_project.Calculator"
    ObjectPath  = "/xyz/openbmc_project/calculator"
        varlinkServiceName = "org.varlink.service"
)

const serviceInterfaceDescription = `interface xyz.openbmc_project.Calculator

type State (
    lastResult: int,
    status: string,
    base: string,
    owner: string,
    objectPath: string,
    serviceName: string
)

method Multiply(x: int, y: ?int) -> (z: int)
method Divide(x: int, y: ?int) -> (z: int)
method Express() -> (z: string)
method Clear() -> ()
method GetState() -> (state: State)
method SetOwner(owner: string) -> ()

error DivisionByZero ()
error PermissionDenied ()
`

type rpcRequest struct {
    Method     string         `json:"method"`
    Parameters map[string]any `json:"parameters,omitempty"`
    Oneway     bool           `json:"oneway,omitempty"`
}

type rpcResponse struct {
    Parameters map[string]any `json:"parameters,omitempty"`
    Error      string         `json:"error,omitempty"`
}

type state struct {
    LastResult int64  `json:"lastResult"`
    Status     string `json:"status"`
    Base       string `json:"base"`
    Owner      string `json:"owner"`
    ObjectPath string `json:"objectPath"`
    Service    string `json:"serviceName"`
}

type result[T any] struct {
    value T
    err   error
}

type calculator struct {
    mu         sync.RWMutex
    lastResult int64
    status     string
    base       string
    owner      string
}

func newCalculator() *calculator {
    return &calculator{
        lastResult: 0,
        status:     "Success",
        base:       "Decimal",
        owner:      "root",
    }
}

func (c *calculator) multiplyAsync(ctx context.Context, x int64, y int64) <-chan result[int64] {
    out := make(chan result[int64], 1)
    go func() {
        defer close(out)
        select {
        case <-ctx.Done():
            out <- result[int64]{err: ctx.Err()}
            return
        default:
        }
        z := x * y
        c.mu.Lock()
        c.lastResult = z
        c.status = "Success"
        c.mu.Unlock()
        out <- result[int64]{value: z}
    }()
    return out
}

func (c *calculator) divideAsync(ctx context.Context, x int64, y int64) <-chan result[int64] {
    out := make(chan result[int64], 1)
    go func() {
        defer close(out)
        select {
        case <-ctx.Done():
            out <- result[int64]{err: ctx.Err()}
            return
        default:
        }
        if y == 0 {
            c.mu.Lock()
            c.status = "Error"
            c.mu.Unlock()
            out <- result[int64]{err: errors.New("DivisionByZero")}
            return
        }
        z := x / y
        c.mu.Lock()
        c.lastResult = z
        c.status = "Success"
        c.mu.Unlock()
        out <- result[int64]{value: z}
    }()
    return out
}

func (c *calculator) expressAsync(ctx context.Context) <-chan result[string] {
    out := make(chan result[string], 1)
    go func() {
        defer close(out)
        select {
        case <-ctx.Done():
            out <- result[string]{err: ctx.Err()}
            return
        default:
        }

        c.mu.RLock()
        value := c.lastResult
        base := c.base
        c.mu.RUnlock()

        switch base {
        case "Binary":
            out <- result[string]{value: fmt.Sprintf("%b", value)}
        case "Heximal":
            out <- result[string]{value: fmt.Sprintf("%x", value)}
        default:
            out <- result[string]{value: fmt.Sprintf("%d", value)}
        }
    }()
    return out
}

func (c *calculator) clearAsync(ctx context.Context) <-chan result[struct{}] {
    out := make(chan result[struct{}], 1)
    go func() {
        defer close(out)
        select {
        case <-ctx.Done():
            out <- result[struct{}]{err: ctx.Err()}
            return
        default:
        }
        c.mu.Lock()
        c.lastResult = 0
        c.status = "Success"
        c.mu.Unlock()
        out <- result[struct{}]{value: struct{}{}}
    }()
    return out
}

func (c *calculator) getStateAsync(ctx context.Context) <-chan result[state] {
    out := make(chan result[state], 1)
    go func() {
        defer close(out)
        select {
        case <-ctx.Done():
            out <- result[state]{err: ctx.Err()}
            return
        default:
        }
        c.mu.RLock()
        st := state{
            LastResult: c.lastResult,
            Status:     c.status,
            Base:       c.base,
            Owner:      c.owner,
            ObjectPath: ObjectPath,
            Service:    ServiceName,
        }
        c.mu.RUnlock()
        out <- result[state]{value: st}
    }()
    return out
}

func (c *calculator) setOwnerAsync(ctx context.Context, owner string) <-chan result[struct{}] {
    out := make(chan result[struct{}], 1)
    go func() {
        defer close(out)
        select {
        case <-ctx.Done():
            out <- result[struct{}]{err: ctx.Err()}
            return
        default:
        }
        if os.Getenv("CALCULATOR_ALLOW_OWNER_CHANGE") != "1" {
            c.mu.Lock()
            c.status = "Error"
            c.mu.Unlock()
            out <- result[struct{}]{err: errors.New("PermissionDenied")}
            return
        }
        c.mu.Lock()
        c.owner = owner
        c.status = "Success"
        c.mu.Unlock()
        out <- result[struct{}]{value: struct{}{}}
    }()
    return out
}

func toInt64(v any) (int64, bool) {
    switch t := v.(type) {
    case float64:
        return int64(t), true
    case int64:
        return t, true
    case int:
        return int64(t), true
    default:
        return 0, false
    }
}

func writeResponse(w io.Writer, resp rpcResponse) error {
    return writeResponseWithDelimiter(w, resp, '\n')
}

func writeResponseWithDelimiter(w io.Writer, resp rpcResponse, delimiter byte) error {
    b, err := json.Marshal(resp)
    if err != nil {
        return err
    }
    _, err = w.Write(append(b, delimiter))
    return err
}

func readMessage(reader *bufio.Reader) ([]byte, byte, error) {
    var payload []byte
    for {
        b, err := reader.ReadByte()
        if err != nil {
            return nil, 0, err
        }
        if b == '\n' || b == 0 {
            return payload, b, nil
        }
        payload = append(payload, b)
    }
}

func dispatch(ctx context.Context, calc *calculator, req rpcRequest) rpcResponse {
    if req.Parameters == nil {
        req.Parameters = map[string]any{}
    }

    switch req.Method {
    case varlinkServiceName + ".GetInfo":
        return rpcResponse{Parameters: map[string]any{
            "vendor":     "GoVarlink",
            "product":    ServiceName,
            "version":    "0.1.0",
            "url":        "https://github.com/JohnBlue-git/GoVarlink",
            "interfaces": []string{ServiceName},
        }}
    case varlinkServiceName + ".GetInterfaceDescription":
        iface, ok := req.Parameters["interface"].(string)
        if !ok {
            return rpcResponse{Error: "org.varlink.service.InvalidParameter"}
        }
        if iface != ServiceName {
            return rpcResponse{Error: "org.varlink.service.InterfaceNotFound"}
        }
        return rpcResponse{Parameters: map[string]any{"description": serviceInterfaceDescription}}
    case ServiceName + ".Multiply":
        x, ok := toInt64(req.Parameters["x"])
        if !ok {
            return rpcResponse{Error: "org.varlink.service.InvalidParameter"}
        }
        y := int64(1)
        if raw, exists := req.Parameters["y"]; exists {
            parsed, ok := toInt64(raw)
            if !ok {
                return rpcResponse{Error: "org.varlink.service.InvalidParameter"}
            }
            y = parsed
        }
        r := <-calc.multiplyAsync(ctx, x, y)
        if r.err != nil {
            return rpcResponse{Error: ServiceName + "." + r.err.Error()}
        }
        return rpcResponse{Parameters: map[string]any{"z": r.value}}
    case ServiceName + ".Divide":
        x, ok := toInt64(req.Parameters["x"])
        if !ok {
            return rpcResponse{Error: "org.varlink.service.InvalidParameter"}
        }
        y := int64(1)
        if raw, exists := req.Parameters["y"]; exists {
            parsed, ok := toInt64(raw)
            if !ok {
                return rpcResponse{Error: "org.varlink.service.InvalidParameter"}
            }
            y = parsed
        }
        r := <-calc.divideAsync(ctx, x, y)
        if r.err != nil {
            return rpcResponse{Error: ServiceName + "." + r.err.Error()}
        }
        return rpcResponse{Parameters: map[string]any{"z": r.value}}
    case ServiceName + ".Express":
        r := <-calc.expressAsync(ctx)
        if r.err != nil {
            return rpcResponse{Error: "org.varlink.service.InternalError"}
        }
        return rpcResponse{Parameters: map[string]any{"z": r.value}}
    case ServiceName + ".Clear":
        _ = <-calc.clearAsync(ctx)
        return rpcResponse{Parameters: map[string]any{}}
    case ServiceName + ".GetState":
        r := <-calc.getStateAsync(ctx)
        if r.err != nil {
            return rpcResponse{Error: "org.varlink.service.InternalError"}
        }
        return rpcResponse{Parameters: map[string]any{"state": r.value}}
    case ServiceName + ".SetOwner":
        owner, ok := req.Parameters["owner"].(string)
        if !ok {
            return rpcResponse{Error: "org.varlink.service.InvalidParameter"}
        }
        r := <-calc.setOwnerAsync(ctx, owner)
        if r.err != nil {
            return rpcResponse{Error: ServiceName + "." + r.err.Error()}
        }
        return rpcResponse{Parameters: map[string]any{}}
    default:
        return rpcResponse{Error: "org.varlink.service.MethodNotImplemented"}
    }
}

func handleConn(calc *calculator, conn net.Conn) {
    defer conn.Close()

    reader := bufio.NewReader(conn)
    for {
        line, delimiter, err := readMessage(reader)
        if err != nil {
            if !errors.Is(err, io.EOF) {
                log.Printf("read error: %v", err)
            }
            return
        }

        var req rpcRequest
        if err := json.Unmarshal(line, &req); err != nil {
            _ = writeResponseWithDelimiter(conn, rpcResponse{Error: "org.varlink.service.InvalidParameter"}, delimiter)
            continue
        }

        resp := dispatch(context.Background(), calc, req)
        if req.Oneway {
            continue
        }
        if err := writeResponseWithDelimiter(conn, resp, delimiter); err != nil {
            log.Printf("write error: %v", err)
            return
        }
    }
}

func main() {
    socketPath := flag.String("socket", "/tmp/calculator-go.sock", "UNIX socket path")
    flag.Parse()

    _ = os.Remove(*socketPath)
    listener, err := net.Listen("unix", *socketPath)
    if err != nil {
        log.Fatalf("listen failed: %v", err)
    }
    defer func() {
        _ = listener.Close()
        _ = os.Remove(*socketPath)
    }()

    log.Printf("Go calculator server on %s", *socketPath)
    calc := newCalculator()

    sigC := make(chan os.Signal, 1)
    signal.Notify(sigC, syscall.SIGINT, syscall.SIGTERM)
    go func() {
        <-sigC
        _ = listener.Close()
    }()

    for {
        conn, err := listener.Accept()
        if err != nil {
            if errors.Is(err, net.ErrClosed) {
                return
            }
            log.Printf("accept error: %v", err)
            continue
        }
        go handleConn(calc, conn)
    }
}