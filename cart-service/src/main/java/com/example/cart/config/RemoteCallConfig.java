package com.example.cart.config;

import org.springframework.cloud.client.loadbalancer.LoadBalanced;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.client.RestTemplate;

@Configuration
public class RemoteCallConfig {


    @LoadBalanced
    @Bean
    public RestTemplate restTemplate(){

        return new RestTemplate();
    }
}
