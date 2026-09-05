package com.example.cart.service;

import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.client.RestTemplate;

import java.util.List;
import java.util.Map;

public interface ICarService {

    public List<Map<String, Object>> getItemsByIds(List<Long> ids);


}
