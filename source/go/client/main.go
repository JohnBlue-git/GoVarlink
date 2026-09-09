package main

import (
    "bufio"
    "encoding/json"
    "errors"
    "flag"
    "fmt"
    "net"
    "os"
    "strconv"
    "time"
)

const serviceName = "xyz.openbmc_project.Calculator"

type rpcRequest struct {
    Method     string         `json:"method"`
    Parameters map[string]any `json:"parameters,omitempty"`
    Oneway     bool           `json:"oneway,omitempty"`
}

type rpcResponse struct {
    Parameters map[string]any `json:"parameters,omitempty"`
    Error      string         `json:"error,omitempty"`
}

type result[T any] struct {
    value T
    err   error
}

type client struct {
    conn   net.Conn
    reader *bufio.Reader
}

func dial(socket string) (*client, error) {
    conn, err := net.Dial("unix", socket)
    if err != nil {
        return nil, err
    }
    return &client{conn: conn, reader: bufio.NewReader(conn)}, nil
}

func (c *client) close() error {
    return c.conn.Close()
}

func (c *client) call(req rpcRequest) (rpcResponse, error) {
    b, err := json.Marshal(req)
    if err != nil {
        return rpcResponse{}, err
    }
    if _, err = fmt.Fprintf(c.conn, "%s\n", b); err != nil {
        return rpcResponse{}, err
    }
    line, err := c.reader.ReadBytes('\n')
    if err != nil {
        return rpcResponse{}, err
    }
    var resp rpcResponse
    if err = json.Unmarshal(line, &resp); err != nil {
        return rpcResponse{}, err
    }
    if resp.Error != "" {
        return resp, errors.New(resp.Error)
    }
    return resp, nil
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

func (c *client) multiplyAsync(x, y int64) <-chan result[int64] {
    out := make(chan result[int64], 1)
    go func() {
        defer close(out)
        resp, err := c.call(rpcRequest{
            Method: serviceName + ".Multiply",
            Parameters: map[string]any{
                "x": x,
                "y": y,
            },
        })
        if err != nil {
            out <- result[int64]{err: err}
            return
        }
        z, ok := toInt64(resp.Parameters["z"])
        if !ok {
            out <- result[int64]{err: errors.New("invalid response")}
            return
        }
        out <- result[int64]{value: z}
    }()
    return out
}

func (c *client) divideAsync(x, y int64) <-chan result[int64] {
    out := make(chan result[int64], 1)
    go func() {
        defer close(out)
        resp, err := c.call(rpcRequest{
            Method: serviceName + ".Divide",
            Parameters: map[string]any{
                "x": x,
                "y": y,
            },
        })
        if err != nil {
            out <- result[int64]{err: err}
            return
        }
        z, ok := toInt64(resp.Parameters["z"])
        if !ok {
            out <- result[int64]{err: errors.New("invalid response")}
            return
        }
        out <- result[int64]{value: z}
    }()
    return out
}

func (c *client) expressAsync() <-chan result[string] {
    out := make(chan result[string], 1)
    go func() {
        defer close(out)
        resp, err := c.call(rpcRequest{Method: serviceName + ".Express"})
        if err != nil {
            out <- result[string]{err: err}
            return
        }
        z, ok := resp.Parameters["z"].(string)
        if !ok {
            out <- result[string]{err: errors.New("invalid response")}
            return
        }
        out <- result[string]{value: z}
    }()
    return out
}

func main() {
    socketPath := flag.String("socket", "/tmp/calculator-go.sock", "UNIX socket path")
    method := flag.String("method", "Multiply", "Multiply|Divide|Express")
    x := flag.Int64("x", 7, "x parameter")
    y := flag.Int64("y", 3, "y parameter")
    iterations := flag.Int("iterations", 1, "benchmark loop count")
    flag.Parse()

    cli, err := dial(*socketPath)
    if err != nil {
        fmt.Fprintf(os.Stderr, "dial failed: %v\n", err)
        os.Exit(1)
    }
    defer cli.close()

    start := time.Now()
    switch *method {
    case "Multiply":
        var value int64
        for i := 0; i < *iterations; i++ {
            r := <-cli.multiplyAsync(*x, *y)
            if r.err != nil {
                fmt.Fprintf(os.Stderr, "%v\n", r.err)
                os.Exit(2)
            }
            value = r.value
        }
        elapsed := time.Since(start)
        fmt.Printf("result=%d elapsed_ms=%d iterations=%d\n", value, elapsed.Milliseconds(), *iterations)
    case "Divide":
        var value int64
        for i := 0; i < *iterations; i++ {
            r := <-cli.divideAsync(*x, *y)
            if r.err != nil {
                fmt.Fprintf(os.Stderr, "%v\n", r.err)
                os.Exit(2)
            }
            value = r.value
        }
        elapsed := time.Since(start)
        fmt.Printf("result=%d elapsed_ms=%d iterations=%d\n", value, elapsed.Milliseconds(), *iterations)
    case "Express":
        var value string
        for i := 0; i < *iterations; i++ {
            r := <-cli.expressAsync()
            if r.err != nil {
                fmt.Fprintf(os.Stderr, "%v\n", r.err)
                os.Exit(2)
            }
            value = r.value
        }
        elapsed := time.Since(start)
        fmt.Printf("result=%s elapsed_ms=%d iterations=%d\n", value, elapsed.Milliseconds(), *iterations)
    default:
        fmt.Fprintf(os.Stderr, "unsupported method: %s\n", strconv.Quote(*method))
        os.Exit(2)
    }
}