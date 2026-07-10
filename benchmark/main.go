package main

import (
	"flag"
	"fmt"
	"net/http"
	"sort"
	"sync"
	"time"
)

type Result struct {
	Latency time.Duration
	Status  int
}

func main() {
	concurrency := flag.Int("concurrency", 10, "Number of concurrent workers")
	duration := flag.Int("duration", 10, "Duration of benchmark in seconds")
	targetURL := flag.String("target-url", "http://localhost:8080/api/limit", "Target URL")
	apiKey := flag.String("api-key", "test-key", "API key for authentication")
	flag.Parse()

	fmt.Printf("Starting benchmark: %s\n", *targetURL)
	fmt.Printf("Concurrency: %d, Duration: %ds\n", *concurrency, *duration)

	results := make(chan Result, 1000000)
	var wg sync.WaitGroup

	startTime := time.Now()
	stopChan := make(chan struct{})

	for i := 0; i < *concurrency; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			client := &http.Client{
				Timeout: 5 * time.Second,
			}
			for {
				select {
				case <-stopChan:
					return
				default:
					reqStart := time.Now()
					req, _ := http.NewRequest("GET", *targetURL, nil)
					req.Header.Set("X-API-Key", *apiKey)

					resp, err := client.Do(req)
					latency := time.Since(reqStart)

					if err != nil {
						// For this benchmark, we count errors as neither 200 nor 429
						results <- Result{Latency: latency, Status: 0}
					} else {
						results <- Result{Latency: latency, Status: resp.StatusCode}
						resp.Body.Close()
					}
				}
			}
		}()
	}

	time.Sleep(time.Duration(*duration) * time.Second)
	close(stopChan)
	wg.Wait()
	close(results)

	var latencies []time.Duration
	var total, ok200, blocked429 int

	for res := range results {
		total++
		if res.Status == http.StatusOK {
			ok200++
		} else if res.Status == http.StatusTooManyRequests {
			blocked429++
		}
		if res.Status != 0 {
			latencies = append(latencies, res.Latency)
		}
	}

	if len(latencies) == 0 {
		fmt.Println("No successful requests recorded.")
		return
	}

	sort.Slice(latencies, func(i, j int) bool {
		return latencies[i] < latencies[j]
	})

	p50 := latencies[len(latencies)*50/100]
	p95 := latencies[len(latencies)*95/100]
	p99 := latencies[len(latencies)*99/100]

	fmt.Println("\n--- Benchmark Summary ---")
	fmt.Printf("Total Requests: %d\n", total)
	fmt.Printf("200 (Allowed): %d\n", ok200)
	fmt.Printf("429 (Blocked):  %d\n", blocked429)
	fmt.Printf("p50 Latency:    %v\n", p50)
	fmt.Printf("p95 Latency:    %v\n", p95)
	fmt.Printf("p99 Latency:    %v\n", p99)
	fmt.Printf("Total Duration:  %v\n", time.Since(startTime))
}
